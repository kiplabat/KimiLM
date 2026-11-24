---
name: sample-publishing
description: Use when publishing or drafting an item on the sample service by hand with its connector (not the publisher pipeline).
---

# Publishing an item with the sample connector

The connector's tools are named like `mcp__sample_account__<tool>`. Changes ask the user first.

1. `whoami` — confirms the account and whether submissions are reviewed.
2. Check for duplicates with `list_items` before creating anything.
3. `create_item` makes a draft. Show the user its link; submit only when they agree.
