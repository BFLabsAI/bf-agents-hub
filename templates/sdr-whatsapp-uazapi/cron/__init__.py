"""Background scheduling package (follow-up dispatcher + cadence advancer).

These run INSIDE app.py: the follow-up dispatcher is an asyncio background task;
the daily cadence advance is an APScheduler job. Logging requirements (decisions
2-4): log every dispatch (INFO), every failure (ERROR + stacktrace), every skip
(WARNING), and every cadence-advance run. Follow-ups are sent THROUGH
agent.arun() (not direct send) so they land in Agno session history, then
logged as a GHL note.
"""
