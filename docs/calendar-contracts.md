# Graph calendar workflow contracts

The v1.0 calendar profile uses the pinned Graph CSDL and REST operation snapshots in
`src/mockazure/cloud/data/graph-workflows`. Calendar groups, calendars, events and meeting
actions persist in PostgreSQL. Resource schema checks are supplemented by writable-field,
action-body, permission and semantic checks; a resource definition alone is not a write contract.

Application tokens use Calendars.ReadBasic.All/Read/ReadWrite. Delegated tokens use
Calendars.ReadBasic/Read/ReadWrite for their own mailbox; another mailbox requires
Calendars.Read.Shared/ReadWrite.Shared and current FullAccess delegation. ReadBasic excludes
body, attachments and extended content. Mail.Send alone grants no calendar access, and
calendar meeting messages do not require a separate Mail.Send grant.

The worker rechecks authority and mailbox/evidence/cloud generations. Each invitation,
response or cancellation commits recipient event and local eventMessage state together.
The event and message have distinct IDs; organizer and attendee events share iCalUId.
Responses with sendResponse=false update only the attendee copy. Recipient source revisions
and cancellation tombstones prevent out-of-order jobs from reviving or cancelling newer state.
Unknown recipients never cause real outbound delivery. Automatic incoming messages do not
produce user Create audit events; public calendar mutations emit item-level facts.

Calendar fixtures participate in cloud state export/import/reset. Imported state has fresh
generations and retires old jobs; pending delivery authority is not exported. Migration069
refuses downgrade while calendar state exists. Completed delivery records participate in pruning.
Calendar history retention and public calendar delta are implemented in task E3.

The E2 checkpoint covers single-instance events and UTC/IANA zones. It intentionally excludes
calendar ACL APIs, Microsoft365 group calendars, event attachments/extensions, new-time proposals,
and online meeting provisioning. Non-default calendars must have no active events before deletion;
groups must be empty. Default containers cannot be deleted. Lists support selection, count and
opaque paging (1000 per page, 10000 per snapshot); unsupported query options fail explicitly.
Event bodies accept at most1MiB, up to500 attendees; action comments at most16384characters.
Recurrence, Windows zones, calendarView/instances/getSchedule/delta belong to E3 and notification
subscriptions to E4. Four-ecosystem native qualification remains pending the combined E gate.


## E3: recurrence, windows and availability

All six Graph pattern types and all three range types are implemented with pinned
tzdata2026.5 transition data and CLDR48.1 Windows/IANA mapping (Unicode-3.0 license included).
Month-end clipping follows the documented Exchange recurrence convention. A recurrence-local
date identifies an occurrence, preserving IDs when the series time changes; iCalUId is shared
between corresponding organizer/attendee occurrences and differs between dates. Exceptions
store only local overrides and cancellation state. Series updates preserve valid overrides;
removing recurrence retires them. Individual edits enforce the documented neighboring-day
restriction. Individual response actions and attendee-list edits remain unsupported.

DST gaps and ambiguous fold wall times fail explicitly; no undocumented Exchange choice is
invented. Calendar windows are limited to366days,10000rows and16MiB, with explicit errors rather
than truncation. Recurrence evaluation is also bounded. Gregorian patterns only. The same
pinned tzdata files are loaded directly on every host, independent of host zone databases.

calendarView/instances pages and primary-calendar calendarView/delta use durable one-hour
tokens, bound to tenant, owner, app, delegated subject, permissions, generation, route and
preferences. Every resume reauthorizes. Delta stores a bounded baseline fingerprint per item;
new rounds compare current window state and return changed records and removal tombstones.
It does not require indefinite event-history retention. Pruning removes old history while
retaining the latest item snapshot. Import resets recipient source revisions with the new
generation so new deliveries cannot be suppressed by old revision numbers.

getSchedule supports up to20 individually authorized local targets and a window shorter
than62days, with intervals5â€“1440minutes. It derives availability and scheduleItems from canonical
events and exceptions, without granting calendar authority through the source mailbox. Private
and basic-read entries omit subject/location. Unknown or unauthorized targets return a local
per-schedule error. Working-hours settings and distribution-list expansion are not simulated.

Native four-ecosystem and Linux/Windows qualification remains pending the shared E gate.

Action parameters accept the exact PascalCase spellings in the pinned Microsoft CSDL and lower-camel REST examples, rejecting ambiguous duplicate names. Primary calendarView/delta also accepts the SDK function URI calendarView/delta(); cursors remain bound to the requested path. Attendees-only updates send meeting messages only to changed attendees; local copies retain the current participant list. Imported events must retain valid timing, recurrence exceptions and unique client/calendar transaction identities.
