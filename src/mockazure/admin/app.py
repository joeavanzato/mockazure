import re
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from mockazure.activity.errors import ActivityError
from mockazure.activity.errors import handle as activity_error_handler
from mockazure.activity.routes import router as activity_router
from mockazure.admin.activity_routes import router as activity_admin_router
from mockazure.admin.arm_routes import router as arm_admin_router
from mockazure.admin.cloud_routes import router as cloud_admin_router
from mockazure.admin.collection_routes import router as collection_admin_router
from mockazure.admin.directory_routes import router as directory_admin_router
from mockazure.admin.errors import AdminError
from mockazure.admin.errors import error_response as admin_error_response
from mockazure.admin.evidence_routes import router as evidence_admin_router
from mockazure.admin.graph_routes import router as graph_admin_router
from mockazure.admin.identity_routes import router as identity_router
from mockazure.admin.job_routes import router as job_router
from mockazure.admin.licensing_routes import router as licensing_admin_router
from mockazure.admin.mail_routes import router as mail_admin_router
from mockazure.admin.maintenance_routes import router as maintenance_admin_router
from mockazure.admin.managed_identity_routes import router as managed_identity_admin_router
from mockazure.admin.population_routes import router as population_admin_router
from mockazure.admin.profile_routes import router as profile_admin_router
from mockazure.admin.resource_routes import router as resource_admin_router
from mockazure.admin.routes import router
from mockazure.admin.security_routes import router as security_admin_router
from mockazure.admin.session_routes import router as session_router
from mockazure.admin.workload_routes import router as workload_admin_router
from mockazure.collections.routes import router as collection_graph_router
from mockazure.config import Settings
from mockazure.console.boundary import authorize_management
from mockazure.console.routes import PUBLIC_PATHS
from mockazure.console.routes import router as console_router
from mockazure.content.routes import router as content_admin_router
from mockazure.db.engine import Database, close_database, create_database
from mockazure.identity.repository import IdentityError
from mockazure.oauth.authorization import router as authorization_router
from mockazure.oauth.device import router as device_router
from mockazure.oauth.discovery import router as discovery_router
from mockazure.oauth.errors import OAuthError, oauth_error_response
from mockazure.oauth.routes import router as oauth_router
from mockazure.providers.arm.activity_routes import router as arm_activity_router
from mockazure.providers.arm.errors import ArmError, handle_arm_error
from mockazure.providers.arm.inventory_routes import router as arm_inventory_router
from mockazure.providers.arm.mutation_routes import router as arm_mutation_router
from mockazure.providers.arm.operations import router as arm_operations_router
from mockazure.providers.arm.probes import router as arm_router
from mockazure.providers.arm.provider_job_routes import router as arm_provider_jobs_router
from mockazure.providers.arm.provider_routes import router as arm_provider_router
from mockazure.providers.arm.rbac_routes import router as arm_rbac_router
from mockazure.providers.arm.resource_handlers import router as arm_resource_router
from mockazure.providers.arm.storage_routes import router as arm_storage_router
from mockazure.providers.exchange.probes import router as exchange_router
from mockazure.providers.exchange.routes import router as exchange_compiled_router
from mockazure.providers.graph.audit_query_routes import router_for as audit_query_router_for
from mockazure.providers.graph.calendar_routes import router as calendar_router
from mockazure.providers.graph.calendar_view_routes import router as calendar_view_router
from mockazure.providers.graph.directory import router_for
from mockazure.providers.graph.drive_mutation_routes import router as drive_mutation_router
from mockazure.providers.graph.drive_routes import router as drive_router
from mockazure.providers.graph.errors import GraphError, graph_error_response
from mockazure.providers.graph.evidence.routes import router_for as evidence_router_for
from mockazure.providers.graph.licensing import router as licensing_graph_router
from mockazure.providers.graph.mail_action_routes import router as mail_action_router
from mockazure.providers.graph.mail_content_routes import router as mail_content_router
from mockazure.providers.graph.mail_mutation_routes import router as mail_mutation_router
from mockazure.providers.graph.mail_routes import router_for as mail_router_for
from mockazure.providers.graph.probes import router as graph_router
from mockazure.providers.graph.security.grant_routes import router_for as grant_router_for
from mockazure.providers.graph.security.method_policy_routes import (
    router_for as method_policy_router,
)
from mockazure.providers.graph.security.method_routes import router_for as method_router_for
from mockazure.providers.graph.security.policy_routes import router_for as policy_router_for
from mockazure.providers.graph.security.report_routes import router_for as registration_router_for
from mockazure.providers.graph.security.role_routes import router_for as role_router_for
from mockazure.providers.graph.security.routes import router_for as security_router_for
from mockazure.providers.graph.site_routes import router as site_router
from mockazure.providers.graph.subscription_routes import router as subscription_router
from mockazure.providers.graph.team_mutation_routes import router as team_mutation_router
from mockazure.providers.graph.team_routes import router as team_router
from mockazure.providers.graph.throttle import enforce_directory_budget
from mockazure.tenants.repository import TenantConflict


def error_response(request, status, code, message):
    path = request.url.path
    if path.startswith("/api/v1.0/"):
        from mockazure.activity.errors import response

        return response(
            ActivityError(
                400 if status == 422 else status, "AF50000" if status >= 500 else "AF20002", message
            )
        )
    if (
        path.startswith(("/v1.0/", "/beta/", "/adminapi/", "/AdminApi/"))
        or path == "/subscriptions"
    ):
        if status == 422:
            status, code = 400, "InvalidRequest"
        return graph_error_response(request, GraphError(status, code, message))
    if (
        "/oauth2/" in path
        or "/discovery/" in path
        or "/.well-known/" in path
        or path in ("/login", "/consent", "/mfa", "/device")
    ):
        oauth_code = (
            "temporarily_unavailable"
            if status == 503
            else ("server_error" if status >= 500 else "invalid_request")
        )
        return oauth_error_response(
            request, OAuthError(oauth_code, message, 400 if status == 422 else status)
        )
    return admin_error_response(request, status, code, message)


def create_app(settings: Settings, database: Database | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        app.state.database = database or create_database(settings)
        try:
            yield
        finally:
            if database is None:
                await close_database(app.state.database)

    app = FastAPI(
        lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False
    )
    app.state.settings = settings
    if database is not None:
        app.state.database = database

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        request.state.request_id = str(uuid4())
        from mockazure.providers.storage.routes import handle as storage_handle
        from mockazure.providers.storage.routes import service_for_request

        storage_service = service_for_request(request, settings)
        if storage_service == "logs":
            from mockazure.providers.loganalytics.routes import handle as logs_handle

            return await logs_handle(request)
        if storage_service == "keyvault":
            from mockazure.providers.keyvault.routes import handle as keyvault_handle

            return await keyvault_handle(request)
        if storage_service is not None:
            return await storage_handle(request, storage_service)
        # Generated Az scope clients join "/{scope}" with an absolute ARM scope.
        # Normalize that one leading slash only for these precise operation shapes;
        # interior duplicate/encoded separators still fail canonical parsing.
        if re.fullmatch(
            r"(?i)//subscriptions/[^/]+(?:/[^/]+)*/providers/"
            r"(microsoft\.resources/tags/default|"
            r"microsoft\.authorization/(roledefinitions|roleassignments)(/[^/]+)?)",
            request.scope["path"],
        ):
            request.scope["path"] = request.scope["path"][1:]
        arm_path = request.scope["path"].lower().split("/")[1] in {"subscriptions", "tenants"}
        request.state.arm_path = arm_path
        if arm_path:
            request.scope["path"] = re.sub(
                r"(?i)/providers/microsoft\.insights/(diagnosticsettingscategories|diagnosticsettings)(?=/|$)",
                lambda match: match.group(0).lower(),
                request.scope["path"],
            )
            if b"%" in request.scope.get("raw_path", b"") or "\\" in request.scope["path"]:
                return await handle_arm_error(
                    request, ArmError(400, "InvalidResourceId", "Encoded ARM paths are unsupported")
                )
            request.scope["path"] = re.sub(
                r"(?i)/providers/microsoft\.insights/eventtypes/management/values(?=/|$)",
                lambda match: match.group(0).lower(),
                request.scope["path"],
            )
            # ARM's reserved path segments are case-insensitive; resource names retain case.
            request.scope["path"] = re.sub(
                r"(?i)(?<=/)(subscriptions|tenants|resourcegroups|resources|providers)(?=/|$)",
                lambda match: match.group(0).lower(),
                request.scope["path"],
            )
            request.scope["path"] = re.sub(
                r"(?i)/providers/microsoft\.resources/(tags/default|operations)(?=/|$)",
                lambda match: match.group(0).lower(),
                request.scope["path"],
            )
            request.scope["path"] = re.sub(
                r"(?i)/providers/microsoft\.authorization/(roledefinitions|roleassignments)(?=/|$)",
                lambda match: match.group(0).lower(),
                request.scope["path"],
            )
            request.scope["path"] = re.sub(
                r"(?i)/providers/microsoft\.storage/(storageaccounts|checknameavailability)(?=/|$)",
                lambda match: match.group(0).lower(),
                request.scope["path"],
            )
        try:
            authorities = request.headers.getlist("host")
            authority = authorities[0].lower() if len(authorities) == 1 else ""
            parsed = re.fullmatch(
                r"([a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)(?::([0-9]{1,5}))?", authority
            )
            if (
                parsed is None
                or parsed[1] not in settings.allowed_hosts
                or (parsed[2] is not None and not 1 <= int(parsed[2]) <= 65535)
            ):
                return error_response(request, 400, "InvalidHost", "Host is not configured")
            if request.url.path.startswith("/_mock") and request.url.path not in (
                "/_mock/health",
                "/_mock/ready",
            ):
                if request.url.path not in PUBLIC_PATHS:
                    request.state.management_identity = await authorize_management(request)
            await enforce_directory_budget(request)
            response = await call_next(request)
        except AdminError as error:
            response = error_response(request, error.status, error.code, error.message)
            if error.status == 401:
                response.headers["WWW-Authenticate"] = "Bearer"
        except GraphError as error:
            response = graph_error_response(request, error)
        except SQLAlchemyError:
            response = error_response(request, 503, "DatabaseUnavailable", "Database is not ready")
        except Exception:
            # Do not propagate raw exception messages into ASGI logs or responses.
            response = error_response(
                request, 500, "InternalError", "Request could not be completed"
            )
        response.headers["x-request-id"] = request.state.request_id
        if arm_path:
            response.headers["x-ms-request-id"] = request.state.request_id
        return response

    @app.exception_handler(GraphError)
    async def graph_error(request, error):
        return graph_error_response(request, error)

    @app.exception_handler(OAuthError)
    async def oauth_error(request, error):
        fact = getattr(error, "authentication_fact", None)
        if fact is not None:
            from mockazure.evidence.authentication import record_authentication_failure

            try:
                await record_authentication_failure(request.app.state.database, fact)
            except (SQLAlchemyError, ValueError, TimeoutError):
                return oauth_error_response(
                    request,
                    OAuthError(
                        "temporarily_unavailable",
                        "Authentication evidence storage is unavailable",
                        503,
                    ),
                )
        return oauth_error_response(request, error)

    @app.exception_handler(IdentityError)
    async def identity_error(request, error):
        return error_response(request, error.status, error.code, error.message)

    @app.exception_handler(AdminError)
    async def admin_error(request, error):
        return error_response(request, error.status, error.code, error.message)

    @app.exception_handler(TenantConflict)
    async def conflict(request, error):
        return error_response(
            request, 409, "TenantConflict", "Tenant slug or domain already exists"
        )

    @app.exception_handler(RequestValidationError)
    async def invalid(request, error):
        if getattr(request.state, "arm_path", False):
            return await handle_arm_error(
                request,
                ArmError(
                    400, "InvalidRequestContent", "Request does not match the supported schema"
                ),
            )
        return error_response(
            request, 422, "ValidationError", "Request does not match the supported schema"
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, error):
        if getattr(request.state, "arm_path", False):
            return await handle_arm_error(
                request, ArmError(503, "DatabaseUnavailable", "Database or schema is not ready")
            )
        return error_response(
            request, 503, "DatabaseUnavailable", "Database or schema is not ready"
        )

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        if getattr(request.state, "arm_path", False):
            return await handle_arm_error(
                request,
                ArmError(
                    501 if error.status_code == 404 else error.status_code,
                    "MockAzureOperationNotImplemented"
                    if error.status_code == 404
                    else "MethodNotAllowed",
                    "This ARM operation is not implemented"
                    if error.status_code == 404
                    else "Method not allowed",
                ),
            )
        if error.status_code == 404 and not request.url.path.startswith("/_mock"):
            return error_response(
                request,
                501,
                "MockAzureOperationNotImplemented",
                "This provider operation is not implemented",
            )
        return error_response(
            request,
            error.status_code,
            "MethodNotAllowed" if error.status_code == 405 else "NotFound",
            "Method not allowed" if error.status_code == 405 else "Resource not found",
        )

    app.add_exception_handler(ActivityError, activity_error_handler)
    app.include_router(activity_router)
    app.include_router(activity_admin_router)
    app.include_router(workload_admin_router)
    app.include_router(content_admin_router)
    app.include_router(mail_admin_router)
    app.include_router(maintenance_admin_router)
    app.include_router(router)
    app.include_router(console_router)
    app.include_router(resource_admin_router)
    app.include_router(profile_admin_router)
    app.include_router(population_admin_router)
    app.include_router(evidence_admin_router)
    app.include_router(collection_admin_router)
    app.include_router(collection_graph_router)
    app.include_router(job_router)
    app.include_router(licensing_admin_router)
    app.include_router(managed_identity_admin_router)
    app.include_router(licensing_graph_router)
    app.add_exception_handler(ArmError, handle_arm_error)
    app.include_router(arm_admin_router)
    app.include_router(cloud_admin_router)
    from mockazure.providers.keyvault.management import router as vault_management_router

    app.include_router(vault_management_router)
    from mockazure.providers.arm.diagnostic_routes import router as diagnostic_router

    app.include_router(diagnostic_router)
    app.include_router(arm_storage_router)
    from mockazure.providers.resourcegraph.routes import router as resourcegraph_router

    app.include_router(resourcegraph_router)
    app.include_router(arm_provider_router)
    app.include_router(arm_router)
    app.include_router(arm_inventory_router)
    app.include_router(arm_activity_router)
    app.include_router(arm_mutation_router)
    app.include_router(arm_operations_router)
    app.include_router(arm_rbac_router)
    app.include_router(arm_provider_jobs_router)
    app.include_router(arm_resource_router)
    app.include_router(session_router)
    app.include_router(identity_router)
    app.include_router(security_admin_router)
    app.include_router(graph_admin_router)
    app.include_router(directory_admin_router)
    app.include_router(discovery_router)
    app.include_router(oauth_router)
    app.include_router(authorization_router)
    app.include_router(device_router)
    app.include_router(graph_router, prefix="/v1.0")
    app.include_router(graph_router, prefix="/beta")
    app.include_router(evidence_router_for("v1.0"))
    app.include_router(evidence_router_for("beta"))
    app.include_router(registration_router_for("v1.0"))
    app.include_router(registration_router_for("beta"))
    app.include_router(method_policy_router())
    app.include_router(method_router_for("v1.0"))
    app.include_router(method_router_for("beta"))
    app.include_router(policy_router_for("v1.0"))
    app.include_router(policy_router_for("beta"))
    app.include_router(role_router_for("v1.0"))
    from mockazure.providers.graph.security.pim_routes import router as pim_router

    app.include_router(pim_router)
    app.include_router(role_router_for("beta"))
    app.include_router(grant_router_for("v1.0"))
    app.include_router(grant_router_for("beta"))
    app.include_router(security_router_for("v1.0"))
    app.include_router(security_router_for("beta"))
    app.include_router(audit_query_router_for("v1.0"))
    app.include_router(audit_query_router_for("beta"))
    app.include_router(team_mutation_router)
    app.include_router(team_router)
    app.include_router(site_router)
    app.include_router(drive_mutation_router)
    app.include_router(drive_router)
    app.include_router(calendar_view_router)
    app.include_router(subscription_router)
    app.include_router(calendar_router)
    app.include_router(mail_action_router)
    app.include_router(mail_mutation_router)
    app.include_router(mail_content_router)
    app.include_router(mail_router_for("v1.0"))
    app.include_router(mail_router_for("beta"))
    from mockazure.providers.graph.intune_routes import router as intune_router

    app.include_router(intune_router)
    from mockazure.providers.graph.object_routes import router as object_router
    from mockazure.providers.graph.security.device_user_routes import router as device_user_router

    app.include_router(device_user_router())
    app.include_router(object_router())
    from mockazure.providers.graph.identity_lifecycle_routes import router as lifecycle_router

    app.include_router(lifecycle_router)
    from mockazure.providers.graph.deleted_routes import router as deleted_router

    app.include_router(deleted_router)
    from mockazure.admin.guest_routes import router as guest_router
    from mockazure.providers.graph.invitation_routes import router as invitation_router

    app.include_router(guest_router)
    from mockazure.admin.hunting_routes import router as hunting_admin_router

    app.include_router(hunting_admin_router)
    from mockazure.providers.graph.hunting_routes import router as hunting_graph_router

    app.include_router(hunting_graph_router)
    app.include_router(invitation_router)
    app.include_router(router_for("v1.0"))
    app.include_router(router_for("beta"))
    app.include_router(exchange_router)
    app.include_router(exchange_compiled_router)
    return app
