# Migration Precondition Evidence

AutoGen-to-Agent-Framework planning must confirm AutoGen imports in the source before changing a direct provider implementation.

## What Happened
For task `autogen-to-agent-framework-20260827220101`, Python source search found no `autogen`, `autogen_agentchat`, `autogen_ext`, or AutoGen agent symbols. The app uses direct `google-genai` calls instead.

## Takeaway
Treat missing source technology as a blocking precondition. Keep the plan conditional and preserve existing public contracts until the source is supplied or the user explicitly approves a broader provider migration.

## History
- 2026-08-28 (nutrition-bot/autogen-to-agent-framework-20260827220101): initial
