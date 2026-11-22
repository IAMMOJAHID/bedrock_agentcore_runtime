# V2 memory demo (local)

- Started: 2026-11-22T06:40:07+00:00
- Memory: `ProductivityAgent_default_TaskMemory-LqBSxx75HT`  Runtime: `arn:aws:bedrock-agentcore:ap-south-1:590183725128:runtime/ProductivityAgent_default_agent-dxt08oE1hB`
- Actors: `USER-A-L1210`, `USER-B-L1210`
- Sessions: A=`v2-local-A-73fac608-30e4-4396-b0a6-d42db295dffb`, B=`v2-local-B-aad327c4-ee2b-4dac-a787-3d1c8d03d26c`, C=`v2-local-C-13aad6a9-9bb6-40c8-b40f-d2410ea5398c`, D=`v2-local-D-d7c39c14-d059-485c-9996-c99da94078cf`, E=`v2-local-E-abbcb1be-a353-4197-9dd0-6cff5b29f5c8`

## 1a Session continuity - request without due date - PASS

Session `A` | actor `USER-A-L1210` | request: `{"prompt": "Create a task for architecture review."}`

**Tool calls:** (none)

**Memory:** history_source=`empty` restored=`0` user_event=`ok` retrieval=`empty` records=`[]`

**Agent response:**

> Hi! To create the task for the architecture review, could you please provide a due date for it?

_Check: no task created without a due date (asks for it)_

## 1b Session continuity - same session follow-up (MEM-03) - PASS

Session `A` | actor `USER-A-L1210` | request: `{"prompt": "What task did I just ask you to create?"}`

**Tool calls:** `create_task({"due_date": "2026-11-24", "title": "Architecture review", "user_due_date_phrase": "next Tuesday"})` -> error

**Memory:** history_source=`local` restored=`0` user_event=`ok` retrieval=`empty` records=`[]`

**Agent response:**

> I'm sorry, but I couldn't create the task for the architecture review because I didn't receive a due date from you. Could you please provide a due date for this task?

_Check: recalls 'architecture review' from session context_

> **Memory check:** local: simulate_restart flag sent with the next request

## 1c Continuity after the agent process is re-created (short-term memory from events) - PASS

Session `A` | actor `USER-A-L1210` | request: `{"prompt": "Remind me: what task did I ask you to create earlier in this conversation?", "simulate_restart": true}`

**Tool calls:** (none)

**Memory:** history_source=`ok` restored=`4` user_event=`ok` retrieval=`empty` records=`[]`

**Agent response:**

> You asked me to create a task for an architecture review, but I couldn't proceed because I didn't have a due date. Could you please provide a due date for this task?

_Check: history restored from AgentCore Memory events: 4 messages_

## 2 Store preference (MEM-04) - PASS

Session `A` | actor `USER-A-L1210` | request: `{"prompt": "Remember that I prefer short bullet-point task summaries."}`

**Tool calls:** (none)

**Memory:** history_source=`local` restored=`0` user_event=`ok` retrieval=`empty` records=`[]`

**Agent response:**

> Understood, I will remember your preference for short bullet-point task summaries. Could you please provide a due date for the architecture review task?

_Check: user event persisted (0000001795329629139#acc1ee2e) and preference acknowledged_

> **Memory check:** long-term extraction for USER-A-L1210: found after ~67s; records=['{"context":"Summarizing tasks or to-do items for the user","preference":"Prefers short bullet-point task summaries","categories":["task summaries","formatting","communication style"]}']

## 3a New session B - create tasks (tool source of truth) - PASS

Session `B` | actor `USER-A-L1210` | request: `{"prompt": "Create a task to prepare the demo slides by Friday and a task to review the budget by Monday."}`

**Tool calls:** `create_task({"due_date": "2026-11-27", "title": "Prepare the demo slides", "user_due_date_phrase": "Friday"})` -> success, `create_task({"due_date": "2026-11-23", "title": "Review the budget", "user_due_date_phrase": "Monday"})` -> success, `list_tasks({"status": "pending"})` -> success

**Memory:** history_source=`empty` restored=`0` user_event=`ok` retrieval=`ok` records=`['Prefers short bullet-point task summaries (applies to: Summarizing tasks or to-do items for the user)']`

**Agent response:**

> Here are your pending tasks:
> 
> 1. **Review the budget** due by Monday, 2026-11-23 (TASK-002)
> 2. **Prepare the demo slides** due by Friday, 2026-11-27 (TASK-001)

_Check: two tasks created_

## 3b New-session recall - bullet format (MEM-06, MEM-10) - PASS

Session `B` | actor `USER-A-L1210` | request: `{"prompt": "Show my tasks in my preferred format."}`

**Tool calls:** `list_tasks({"status": "all"})` -> success

**Memory:** history_source=`local` restored=`0` user_event=`ok` retrieval=`ok` records=`['Prefers short bullet-point task summaries (applies to: Summarizing tasks or to-do items for the user)']`

**Agent response:**

> Here are all your tasks in a short bullet-point format:
> 
> - **TASK-002**: Review the budget, due by Monday, 2026-11-23
> - **TASK-001**: Prepare the demo slides, due by Friday, 2026-11-27

_Check: retrieved preference from long-term memory, listed live tasks via tool, bullet format_

## 4 Isolation - USER-B (MEM-07) - PASS

Session `C` | actor `USER-B-L1210` | request: `{"prompt": "What output format do I prefer?"}`

**Tool calls:** (none)

**Memory:** history_source=`empty` restored=`0` user_event=`ok` retrieval=`empty` records=`[]`

**Agent response:**

> I currently have no stored preferences for you regarding the output format. However, I can remember your preference for future sessions. If you have a specific format in mind, please let me know, and I will note it for future reference.
> 
> 
> 
> Hi there! I currently have no stored preferences for you regarding the output format. However, I can remember your preference for future sessions. If you have a specific format in mind, please let me know, and I will note it for future reference.

_Check: USER-B namespace empty; USER-A preference not revealed_

> **Memory check:** records in USER-B namespace: 0

## 5a Preference update stated (MEM-08) - PASS

Session `B` | actor `USER-A-L1210` | request: `{"prompt": "From now on, use a table rather than bullet points."}`

**Tool calls:** `list_tasks({"status": "all"})` -> success

**Memory:** history_source=`local` restored=`0` user_event=`ok` retrieval=`ok` records=`['Prefers short bullet-point task summaries (applies to: Summarizing tasks or to-do items for the user)']`

**Agent response:**

> Here are all your tasks in a table format:
> 
> | Task ID | Title | Due Date | Status |
> | --- | --- | --- | --- |
> | TASK-002 | Review the budget | 2026-11-23 | Pending |
> | TASK-001 | Prepare the demo slides | 2026-11-27 | Pending |

_Check: update persisted as event_

> **Memory check:** updated preference extraction: found after ~84s; records now=['{"context":"Presenting lists of tasks or other structured information; user later stated a preference for tables over bullet points from 2026-11-22T06:41:54.442Z onward, superseding the earlier bullet-point preference","preference":"Prefers tables rather than bullet-point lists for task lists and structured information","categories":["task summaries","response formatting","output style","communication style","task management"]}']

## 6a New session D - create task - PASS

Session `D` | actor `USER-A-L1210` | request: `{"prompt": "Create a task to send the weekly report by Friday."}`

**Tool calls:** `create_task({"due_date": "2026-11-27", "title": "Send the weekly report", "user_due_date_phrase": "Friday"})` -> success

**Memory:** history_source=`empty` restored=`0` user_event=`ok` retrieval=`ok` records=`['Prefers tables rather than bullet-point lists for task lists and structured information (applies to: Presenting lists of tasks or other structured information; user later stated a preference for tables over bullet points from 2026-11-22T06:41:54.442Z onward, superseding the earlier bullet-point preference)']`

**Agent response:**

> The task to send the weekly report by Friday has been created. Here is the task information:
> 
> | Task ID | Title | Due Date | Status |
> | --- | --- | --- | --- |
> | TASK-003 | Send the weekly report | 2026-11-27 | Pending |

_Check: task created_

## 6b Tool + updated memory - table format (MEM-08, MEM-10) - PASS

Session `D` | actor `USER-A-L1210` | request: `{"prompt": "List my pending tasks using my preferred format."}`

**Tool calls:** `list_tasks({"status": "pending"})` -> success

**Memory:** history_source=`local` restored=`0` user_event=`ok` retrieval=`ok` records=`['Prefers tables rather than bullet-point lists for task lists and structured information (applies to: Presenting lists of tasks or other structured information; user later stated a preference for tables over bullet points from 2026-11-22T06:41:54.442Z onward, superseding the earlier bullet-point preference)']`

**Agent response:**

> Here are your pending tasks in a table format:
> 
> | Task ID | Title | Due Date | Status |
> | --- | --- | --- | --- |
> | TASK-002 | Review the budget | 2026-11-23 | Pending |
> | TASK-001 | Prepare the demo slides | 2026-11-27 | Pending |
> | TASK-003 | Send the weekly report | 2026-11-27 | Pending |

_Check: live tasks via list_tasks, rendered as a table (latest preference)_

## 7a Memory retrieval failure (MEM-09) - PASS

Session `E` | actor `USER-A-L1210` | request: `{"prompt": "What output format do I prefer?", "simulate_failure": ["memory_retrieve"]}`

**Tool calls:** (none)

**Memory:** history_source=`empty` restored=`0` user_event=`ok` retrieval=`unavailable` records=`[]`

**Agent response:**

> Please let me know your preferred output format, and I will note it for you. Note: I couldn't access your stored preferences just now (the memory service is unavailable), so I can't tell you what you prefer and haven't applied any remembered preferences. Please try again shortly.

_Check: retrieval unavailable; reply states it and claims no preference (guard applied=True)_

## 7b Memory write failure (MEM-09) - PASS

Session `E` | actor `USER-A-L1210` | request: `{"prompt": "Remember that I usually plan my work in the morning.", "simulate_failure": ["memory_write"]}`

**Tool calls:** (none)

**Memory:** history_source=`local` restored=`0` user_event=`unavailable` retrieval=`ok` records=`['Prefers tables rather than bullet-point lists for task lists and structured information (applies to: Presenting lists of tasks or other structured information; user later stated a preference for tables over bullet points from 2026-11-22T06:41:54.442Z onward, superseding the earlier bullet-point preference)']`

**Agent response:**

> Note: I couldn't save this to long-term memory (the memory service is unavailable right now), so I won't be able to recall it in future sessions. Please tell me again later.

_Check: write failed; reply says it could not save, no false claim (guard applied=True)_
