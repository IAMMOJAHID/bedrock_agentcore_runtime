# V2 memory demo (remote)

- Started: 2026-11-22T06:39:55+00:00
- Memory: `ProductivityAgent_default_TaskMemory-LqBSxx75HT`  Runtime: `arn:aws:bedrock-agentcore:ap-south-1:590183725128:runtime/ProductivityAgent_default_agent-dxt08oE1hB`
- Actors: `USER-A-R1209`, `USER-B-R1209`
- Sessions: A=`v2-remote-A-60155c7d-bc66-4ba0-b66e-cbb47091ef39`, B=`v2-remote-B-9f940cfe-3c58-459e-a8bc-e99fb2e34fab`, C=`v2-remote-C-c2590e6a-0bb9-48f7-9886-906bfa885355`, D=`v2-remote-D-0f467e9d-52a1-4a38-874f-6010cec2f248`, E=`v2-remote-E-20063898-11ef-4fe3-bdf6-dd518e22642b`

## 1a Session continuity - request without due date - PASS

Session `A` | actor `USER-A-R1209` | request: `{"prompt": "Create a task for architecture review."}`

**Tool calls:** (none)

**Memory:** history_source=`empty` restored=`0` user_event=`ok` retrieval=`empty` records=`[]`

**Agent response:**

> What date would you like to set as the due date for the architecture review task?

_Check: no task created without a due date (asks for it)_

## 1b Session continuity - same session follow-up (MEM-03) - PASS

Session `A` | actor `USER-A-R1209` | request: `{"prompt": "What task did I just ask you to create?"}`

**Tool calls:** `create_task({"due_date": "2026-11-22", "title": "architecture review", "user_due_date_phrase": "today"})` -> error

**Memory:** history_source=`local` restored=`0` user_event=`ok` retrieval=`empty` records=`[]`

**Agent response:**

> I'm sorry, but I need a due date to create the task. Could you please specify when you would like to complete the architecture review?

_Check: recalls 'architecture review' from session context_

> **Memory check:** remote: stop-runtime-session -> {     "runtimeSessionId": "v2-remote-A-60155c7d-bc66-4ba0-b66e-cbb47091ef39",     "statusCode": 200 }

## 1c Continuity after the agent process is re-created (short-term memory from events) - PASS

Session `A` | actor `USER-A-R1209` | request: `{"prompt": "Remind me: what task did I ask you to create earlier in this conversation?"}`

**Tool calls:** (none)

**Memory:** history_source=`ok` restored=`4` user_event=`ok` retrieval=`empty` records=`[]`

**Agent response:**

> You asked me to create a task for an architecture review, but I need a due date to proceed. Could you please provide the due date for this task?

_Check: history restored from AgentCore Memory events: 4 messages_

## 2 Store preference (MEM-04) - PASS

Session `A` | actor `USER-A-R1209` | request: `{"prompt": "Remember that I prefer short bullet-point task summaries."}`

**Tool calls:** (none)

**Memory:** history_source=`local` restored=`0` user_event=`ok` retrieval=`empty` records=`[]`

**Agent response:**

> Acknowledged, I will remember your preference for short bullet-point task summaries. Please provide the due date for the architecture review task so I can create it.

_Check: user event persisted (0000001795329645372#71002bf5) and preference acknowledged_

> **Memory check:** long-term extraction for USER-A-R1209: found after ~67s; records=['{"context":"Summarizing tasks or to-do items for the user","preference":"Prefers short bullet-point task summaries","categories":["task summaries","formatting","communication style"]}']

## 3a New session B - create tasks (tool source of truth) - PASS

Session `B` | actor `USER-A-R1209` | request: `{"prompt": "Create a task to prepare the demo slides by Friday and a task to review the budget by Monday."}`

**Tool calls:** `create_task({"due_date": "2026-11-27", "title": "Prepare the demo slides", "user_due_date_phrase": "Friday"})` -> success, `create_task({"due_date": "2026-11-23", "title": "Review the budget", "user_due_date_phrase": "Monday"})` -> success, `list_tasks({"status": "pending"})` -> success

**Memory:** history_source=`empty` restored=`0` user_event=`ok` retrieval=`ok` records=`['Prefers short bullet-point task summaries (applies to: Summarizing tasks or to-do items for the user)']`

**Agent response:**

> Here are your pending tasks:
> 
> 1. **Review the budget** due on Monday, 2026-11-23.
> 2. **Prepare the demo slides** due on Friday, 2026-11-27.

_Check: two tasks created_

## 3b New-session recall - bullet format (MEM-06, MEM-10) - PASS

Session `B` | actor `USER-A-R1209` | request: `{"prompt": "Show my tasks in my preferred format."}`

**Tool calls:** `list_tasks({"status": "all"})` -> success

**Memory:** history_source=`local` restored=`0` user_event=`ok` retrieval=`ok` records=`['Prefers short bullet-point task summaries (applies to: Summarizing tasks or to-do items for the user)']`

**Agent response:**

> Here are all your tasks in a short bullet-point format:
> 
> - **TASK-002**: Review the budget, due on 2026-11-23.
> - **TASK-001**: Prepare the demo slides, due on 2026-11-27.

_Check: retrieved preference from long-term memory, listed live tasks via tool, bullet format_

## 4 Isolation - USER-B (MEM-07) - PASS

Session `C` | actor `USER-B-R1209` | request: `{"prompt": "What output format do I prefer?"}`

**Tool calls:** (none)

**Memory:** history_source=`empty` restored=`0` user_event=`ok` retrieval=`empty` records=`[]`

**Agent response:**

> I currently have no stored preferences for you regarding the output format. However, I can remember your preference for future sessions. If you have a specific format in mind, please let me know, and I will note it.

_Check: USER-B namespace empty; USER-A preference not revealed_

> **Memory check:** records in USER-B namespace: 0

## 5a Preference update stated (MEM-08) - PASS

Session `B` | actor `USER-A-R1209` | request: `{"prompt": "From now on, use a table rather than bullet points."}`

**Tool calls:** `list_tasks({"status": "all"})` -> success

**Memory:** history_source=`local` restored=`0` user_event=`ok` retrieval=`ok` records=`['Prefers short bullet-point task summaries (applies to: Summarizing tasks or to-do items for the user)']`

**Agent response:**

> Here are all your tasks in a table format:
> 
> | Task ID | Title | Due Date | Status |
> |---------|-------|----------|--------|
> | TASK-002 | Review the budget | 2026-11-23 | Pending |
> | TASK-001 | Prepare the demo slides | 2026-11-27 | Pending |

_Check: update persisted as event_

> **Memory check:** updated preference extraction: found after ~68s; records now=['{"context":"Summarizing or listing tasks or to-do items for the user in future responses; supersedes earlier preference for short bullet-point summaries","preference":"Prefers tasks to be shown in a table rather than short bullet-point summaries","categories":["task summaries","formatting","communication style","task display","output format"]}']

## 6a New session D - create task - PASS

Session `D` | actor `USER-A-R1209` | request: `{"prompt": "Create a task to send the weekly report by Friday."}`

**Tool calls:** `create_task({"due_date": "2026-11-27", "title": "Send the weekly report", "user_due_date_phrase": "Friday"})` -> success

**Memory:** history_source=`empty` restored=`0` user_event=`ok` retrieval=`ok` records=`['Prefers tasks to be shown in a table rather than short bullet-point summaries (applies to: Summarizing or listing tasks or to-do items for the user in future responses; supersedes earlier preference for short bullet-point summaries)']`

**Agent response:**

> The task "Send the weekly report" has been created and is due on Friday, 2026-11-27. Here is the task:
> 
> | Task ID | Title | Due Date | Status |
> | --- | --- | --- | --- |
> | TASK-001 | Send the weekly report | 2026-11-27 | Pending |

_Check: task created_

## 6b Tool + updated memory - table format (MEM-08, MEM-10) - PASS

Session `D` | actor `USER-A-R1209` | request: `{"prompt": "List my pending tasks using my preferred format."}`

**Tool calls:** `list_tasks({"status": "pending"})` -> success

**Memory:** history_source=`local` restored=`0` user_event=`ok` retrieval=`ok` records=`['Prefers tasks to be shown in a table rather than short bullet-point summaries (applies to: Summarizing or listing tasks or to-do items for the user in future responses; supersedes earlier preference for short bullet-point summaries)']`

**Agent response:**

> Here are your pending tasks:
> 
> | Task ID | Title | Due Date | Status |
> | --- | --- | --- | --- |
> | TASK-001 | Send the weekly report | 2026-11-27 | Pending |

_Check: live tasks via list_tasks, rendered as a table (latest preference)_

## 7a Memory retrieval failure (MEM-09) - PASS

Session `E` | actor `USER-A-R1209` | request: `{"prompt": "What output format do I prefer?", "simulate_failure": ["memory_retrieve"]}`

**Tool calls:** (none)

**Memory:** history_source=`empty` restored=`0` user_event=`ok` retrieval=`unavailable` records=`[]`

**Agent response:**

> Please let me know your preferred output format, and I will note it for you. Please let me know your preferred output format, and I will note it for you. Note: I couldn't access your stored preferences just now (the memory service is unavailable), so I can't tell you what you prefer and haven't applied any remembered preferences. Please try again shortly.

_Check: retrieval unavailable; reply states it and claims no preference (guard applied=True)_

## 7b Memory write failure (MEM-09) - PASS

Session `E` | actor `USER-A-R1209` | request: `{"prompt": "Remember that I usually plan my work in the morning.", "simulate_failure": ["memory_write"]}`

**Tool calls:** (none)

**Memory:** history_source=`local` restored=`0` user_event=`unavailable` retrieval=`ok` records=`['Prefers tasks to be shown in a table rather than short bullet-point summaries (applies to: Summarizing or listing tasks or to-do items for the user in future responses; supersedes earlier preference for short bullet-point summaries)']`

**Agent response:**

> Is there anything else you would like to plan for today? Note: I couldn't save this to long-term memory (the memory service is unavailable right now), so I won't be able to recall it in future sessions. Please tell me again later.

_Check: write failed; reply says it could not save, no false claim (guard applied=True)_
