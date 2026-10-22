---
name: LLM and schedules
description: Which stages use OpenRouter web search, schedule gating, IBKR multi-account NAV
---
- Stages in WEB_SEARCH_STAGES (chat, catalyst_outlook, draft_thesis) get the web_search tool; DeepSeek sometimes prints raw "DSML" tool calls as text, so the loop nudges a final answer.
- IBKR advisor Flex query returns ~30 account statements; NAV is summed across them and positions merged by ticker (user chose combined portfolio).
- Background loops (IBKR 6 AM ET daily, catalysts Sunday 6 PM ET) start only when REPLIT_DEPLOYMENT or combined role is set, so the dev workspace never writes scheduled data to the shared Turso DB.
**Why:** dev and prod share one Turso DB. **How to apply:** test jobs manually; publishing activates schedules.
