# Copilot CLI Tool Mapping

Resolve skill operations against the tools and argument schemas exposed by the active Copilot CLI session. A mapping is guidance for choosing the current capability, not a promise that every version or configuration exposes the same tool names.

| Skill operation | Selection rule |
| --- | --- |
| Read, create or edit files | Use the exposed file reader or editor and its current path arguments. |
| Run shell commands | Use the exposed shell tool; follow its session and output contract for long-running commands. |
| Search files or text | Use the available search tool or `rg` through the permitted shell. |
| Invoke a skill | Use an actual skill invocation when available; otherwise follow the applicable body-loading contract. |
| Fetch or search the web | Use an available fetch/search capability and retain source locators. A fetch does not automatically provide a search capability. |
| Dispatch or inspect delegates | Use the current agent tool and documented role/lifecycle parameters, within delegation authority. |
| Track a plan | Use an exposed planning capability or the current authorized task record. |
| Access GitHub | Use an available connected GitHub tool or authorized CLI; tool presence does not grant permission to publish. |

## Missing capabilities

Read the session's tool descriptions before selecting a name or parameter. If a required operation has no supported capability, preserve its unresolved status and use an authorized equivalent when one exists. Do not invent a delegate result, direct-database workaround, memory write or configuration change to imitate an unavailable tool.

The same user authorization, repository boundaries, source-specific verification and credential-helper rules apply regardless of the host's tool names.
