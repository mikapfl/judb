Output from the debuggee is now HTML-escaped before it reaches the browser.
Previously a program that printed markup (or a repr containing it) had that
markup rendered as part of judb's own page instead of shown as text.
