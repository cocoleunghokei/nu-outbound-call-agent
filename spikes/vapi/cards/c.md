# Card C: Mid-call controls

You are the host at Tatte Bakery. An AI assistant will call to book a table for 4 on Friday at 7pm for Alex Chen (fake test data).

Say these lines in order:

1. On answering: "Tatte Bakery, how can I help?"
2. After the request: "Friday at 7pm is full. Let me see what else I can offer."
3. Pause about 10 seconds, then: "I'm still looking."
4. If the agent asks about Saturday (the operator injects this): "Saturday at 7pm is open. Want it?"
5. Then wait. The operator will pause the call and then end it. Keep quietly waiting on the line until the call drops. Do not hang up.

If the agent never asks about Saturday, do not offer it. Say: "Friday is full, anything else?"
