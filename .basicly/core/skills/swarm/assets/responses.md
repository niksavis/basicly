# Findings and responses

Answer every finding before a teardown ends. Copy this table into the report. Every row carries the finding's evidence, also when it is declined, so the user sees what was set aside.

| # | Severity | Finding | Evidence | Response | Detail |
| --- | --- | --- | --- | --- | --- |
| 1 | important | ... | path:line or ran: ... | fixed | commit or diff that fixes it |
| 2 | important | ... | path:line or ran: ... | contested | the evidence that the finding is wrong |
| 3 | nit | ... | path:line or ran: ... | declined | why the cost is higher than the gain |
| 4 | important | ... | path:line or ran: ... | escalated | the decision the user must make |

Responses:

- **fixed**: the change is made and the check that proves it ran.
- **contested**: counter-evidence is attached. The author may contest. A fresh agent decides each contest once; its answer stands.
- **declined**: for nit and pre-existing findings. An important finding is declined only when the user says so, marked "declined by the user".
- **escalated**: the user decides. Give one recommendation.
