
# M5 final review

One fresh independent read-only reviewer inspected 807ac20..b1278cb.
No Critical finding. All two Important findings and one Minor finding were adopted
in one consolidated regression-first fix pass; there is no second review.

1. Important: preview role-assignment pages used a stable-version binding.
   Bind the validated request version, preserve it in nextLink and reject replay
   using the other supported version. Both directions have integration tests.
2. Important: the denied collector oracle accepted unrelated subscription-named
   exceptions. Permit only the observed explicit PSArgumentException and exact
   configured-subscription not-found message. Independent 403 preflight and no
   output remain required. Discovered collection may not use this exception.
   Current saved cases already matched the narrow allowance; this fixes future
   false-positive acceptance.
3. Minor: remove obsolete contract documentation saying routes were unimplemented.

Before fixes: full unit/integration suite 675 passed in 668.75 seconds.
Review regressions: three failures before fixes; ten passed in 11.80 seconds after.
Current-source qualification is rerun after committing the fixes.

Declined judgments and rulings:
- Encoded names, arbitrary providers/custom roles, other locales/versions and
  subscription-level inventory resources remain outside the declared profile.
- Terminal nextLink omission, HEAD, generated double slashes, unchanged PATCH
  name/empty properties and ancestor-qualified definitions retain documented
  pinned-contract/original-client rulings.
- No concrete current-identity staleness during mutation lock waits established;
  grant checks execute after locks.
- Deletion cascades descendant grants; operation polling requires current access.
- Inventory restart on mutation and Activity shrinkage after revocation are
  deliberate current-permission behavior.
- Native comparison supplements independent historical fixtures and independently
  checked create/delete facts; no collector output is repaired.
- Activity selection follows pinned fields plus the pinned-example id exception.
- Additional inventory name/type limits prevent the suspected maximum-scope
  suffix overflow trigger.
- Concurrency, transitive grants, prefix boundaries, failure facts, lease loss,
  retention/reset and exact publication paging had no further confirmed defects.
- M4's four collector failures remain mandatory combined-release blockers.
- M6 cleanup and scale gates are later work, not claimed by M5.
