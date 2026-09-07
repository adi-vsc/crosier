---
name: check
description: Run a Crosier direction check on this session now, instead of waiting for the call threshold.
---

Run this command once, then continue with whatever you were doing:

```
crosier check
```

If `crosier` is not on PATH, the CLI has not been installed. Say so in one line
and stop — do not try to find it, install it, or write the trigger file by hand.
It installs with `pip install -e .` from a Crosier checkout.

Otherwise: do not report on, summarise, or act on the output. The command only
leaves a request behind; the check itself runs in the background a moment later
and announces itself if it has anything to say. Confirm in one short line that
the check was requested, then resume the previous task.
