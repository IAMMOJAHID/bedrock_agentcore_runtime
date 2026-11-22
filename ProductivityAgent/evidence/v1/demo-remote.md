# Demo run (remote)

- Started: 2026-11-18T15:51:25+00:00
- Session ID: `demo-remote-ec59bc52-f11c-4255-bef2-e14a666f39f2`

## S1. Create a task (AC-03) - PASS

**Request:** `{"prompt": "Create a task to complete the architecture review by Friday."}`

**Tool calls:**

- `create_task({"due_date": "2026-11-20", "title": "Complete the architecture review", "user_due_date_phrase": "by Friday"})` -> `{"status": "success", "message": "Task TASK-001 created.", "task": {"task_id": "TASK-001", "title": "Complete the architecture review", "due_date": "2026-11-20", "status": "pending", "created_at": "2026-11-18T15:51:42+00:00", "completed_at": null}, "note": "The task list has changed. If the user's request also asks to see, show or list tasks, call list_tasks now - do not list tasks from memory."}`

**Agent response:**

> The task "Complete the architecture review" has been created and is due by Friday, 2026-11-20.

_Check: create_task succeeded_

## S2. Retrieve pending tasks (AC-04) - PASS

**Request:** `{"prompt": "Show me all my pending tasks."}`

**Tool calls:**

- `list_tasks({"status": "pending"})` -> `{"status": "success", "filter": "pending", "count": 1, "tasks": [{"task_id": "TASK-001", "title": "Complete the architecture review", "due_date": "2026-11-20", "status": "pending", "created_at": "2026-11-18T15:51:42+00:00", "completed_at": null}]}`

**Agent response:**

> Here are your pending tasks:
> 
> - **TASK-001**: Complete the architecture review, due by Friday, 2026-11-20.

_Check: list_tasks succeeded_

## S3. Multi-step interaction (AC-06) - PASS

**Request:** `{"prompt": "Create a task for reviewing the AgentCore architecture due next Monday and then show me all my pending tasks."}`

**Tool calls:**

- `create_task({"due_date": "2026-11-23", "title": "Review the AgentCore architecture", "user_due_date_phrase": "next Monday"})` -> `{"status": "success", "message": "Task TASK-002 created.", "task": {"task_id": "TASK-002", "title": "Review the AgentCore architecture", "due_date": "2026-11-23", "status": "pending", "created_at": "2026-11-18T15:51:53+00:00", "completed_at": null}, "note": "The task list has changed. If the user's request also asks to see, show or list tasks, call list_tasks now - do not list tasks from memory."}`
- `list_tasks({"status": "pending"})` -> `{"status": "success", "filter": "pending", "count": 2, "tasks": [{"task_id": "TASK-001", "title": "Complete the architecture review", "due_date": "2026-11-20", "status": "pending", "created_at": "2026-11-18T15:51:42+00:00", "completed_at": null}, {"task_id": "TASK-002", "title": "Review the AgentCore architecture", "due_date": "2026-11-23", "status": "pending", "created_at": "2026-11-18T15:51:53+00:00", "completed_at": null}]}`

**Agent response:**

> The task "Review the AgentCore architecture" has been created and is due by next Monday, 2026-11-23. Here are all your pending tasks:
> 
> - **TASK-001**: Complete the architecture review, due by Friday, 2026-11-20.
> - **TASK-002**: Review the AgentCore architecture, due by next Monday, 2026-11-23.

_Check: create_task AND list_tasks in one turn_

## S4. Complete a task (AC-05) - PASS

**Request:** `{"prompt": "Mark task TASK-001 as completed."}`

**Tool calls:**

- `complete_task({"task_id": "TASK-001"})` -> `{"status": "success", "message": "Task TASK-001 marked as completed.", "changed": true, "task": {"task_id": "TASK-001", "title": "Complete the architecture review", "due_date": "2026-11-20", "status": "completed", "created_at": "2026-11-18T15:51:42+00:00", "completed_at": "2026-11-18T15:51:58+00:00"}, "note": "The task list has changed. If the user's request also asks to see, show or list tasks, call list_tasks now - do not list tasks from memory."}`

**Agent response:**

> Task TASK-001 has been marked as completed.

_Check: complete_task succeeded_

## S5. Invalid input - unknown id (AC-08) - PASS

**Request:** `{"prompt": "Complete task TASK-999."}`

**Tool calls:**

- `complete_task({"task_id": "TASK-999"})` -> `{"status": "error", "error_type": "not_found", "message": "Task TASK-999 does not exist."}`

**Agent response:**

> I'm sorry, but the task TASK-999 does not exist. Please check the task ID and try again.

_Check: complete_task returned not_found_

## S5b. Invalid input - missing due date (AC-08) - PASS

**Request:** `{"prompt": "Create a task to call the vendor."}`

**Tool calls:**

- (none)

**Agent response:**

> I need the due date for the task to call the vendor. Could you please provide the due date?

_Check: no task created without a due date_

## S5c. Invalid input - impossible date (AC-08) - PASS

**Request:** `{"prompt": "Create a task to update the roadmap due on 2026-02-30."}`

**Tool calls:**

- `create_task({"due_date": "2026-02-30", "title": "Update the roadmap", "user_due_date_phrase": "2026-02-30"})` -> `{"status": "error", "error_type": "validation_error", "message": "'2026-02-30' is not a valid date. Use the format YYYY-MM-DD, e.g. 2026-11-20."}`

**Agent response:**

> Sorry, but the date 2026-02-30 is not valid. Please provide a valid date in the format YYYY-MM-DD.

_Check: invalid date rejected_

## S6. Contextual interaction (AC-07) - PASS

**Request:** `{"prompt": "Also mark the AgentCore architecture review task I created earlier as completed, then show me everything including completed tasks."}`

**Tool calls:**

- `complete_task({"task_id": "TASK-002"})` -> `{"status": "success", "message": "Task TASK-002 marked as completed.", "changed": true, "task": {"task_id": "TASK-002", "title": "Review the AgentCore architecture", "due_date": "2026-11-23", "status": "completed", "created_at": "2026-11-18T15:51:53+00:00", "completed_at": "2026-11-18T15:52:22+00:00"}, "note": "The task list has changed. If the user's request also asks to see, show or list tasks, call list_tasks now - do not list tasks from memory."}`
- `list_tasks({"status": "all"})` -> `{"status": "success", "filter": "all", "count": 2, "tasks": [{"task_id": "TASK-001", "title": "Complete the architecture review", "due_date": "2026-11-20", "status": "completed", "created_at": "2026-11-18T15:51:42+00:00", "completed_at": "2026-11-18T15:51:58+00:00"}, {"task_id": "TASK-002", "title": "Review the AgentCore architecture", "due_date": "2026-11-23", "status": "completed", "created_at": "2026-11-18T15:51:53+00:00", "completed_at": "2026-11-18T15:52:22+00:00"}]}`

**Agent response:**

> The task "Review the AgentCore architecture" has been marked as completed. Here are all your tasks, including completed ones:
> 
> - **TASK-001**: Complete the architecture review, due by Friday, 2026-11-20, **completed**.
> - **TASK-002**: Review the AgentCore architecture, due by next Monday, 2026-11-23, **completed**.

_Check: resolved 'the AgentCore task' to TASK-002 from session context_

## S7. Tool failure (AC-09) - list_tasks backend down - PASS

**Request:** `{"prompt": "Show me all my pending tasks.", "simulate_failure": ["list_tasks"]}`

**Tool calls:**

- `list_tasks({"status": "pending"})` -> `{"status": "error", "error_type": "tool_failure", "message": "The list_tasks operation failed: Task backend unavailable while executing list_tasks (simulated failure).. The operation was NOT performed."}`

**Agent response:**

> I'm sorry, but I'm currently unable to show your pending tasks due to a temporary issue with the task backend. Please try again later.

_Check: list_tasks failed and agent reported it_

## S7b. Tool failure (AC-09) - create_task backend down - PASS

**Request:** `{"prompt": "Create a task to prepare the demo slides by Friday.", "simulate_failure": ["create_task"]}`

**Tool calls:**

- `create_task({"due_date": "2026-11-20", "title": "Prepare the demo slides", "user_due_date_phrase": "by Friday"})` -> `{"status": "error", "error_type": "tool_failure", "message": "The create_task operation failed: Task backend unavailable while executing create_task (simulated failure).. The operation was NOT performed."}`

**Agent response:**

> I'm sorry, but I'm currently unable to create the task "Prepare the demo slides" due to a temporary issue with the task backend. Please try again later.

_Check: create_task failed, no false success claim_

## S7c. Recovery after failure injection is disabled - PASS

**Request:** `{"prompt": "The backend is back. Show me my pending tasks.", "simulate_failure": []}`

**Tool calls:**

- `list_tasks({"status": "pending"})` -> `{"status": "success", "filter": "pending", "count": 0, "tasks": []}`

**Agent response:**

> You currently have no pending tasks.

_Check: list_tasks succeeds again_
