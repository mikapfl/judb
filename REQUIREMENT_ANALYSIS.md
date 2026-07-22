We have identified a key conflict/gap when operationalising scientific code. Scientists like jupyter notebooks a lot. Everything is inline, iteration is very fast, and it is trivial to e.g. plot intermediate results right where the code is. However, notebooks don't scale to larger projects (it is hard to call code within notebooks and build longer pipelines, projects like papermill are not the solution because the interface a notebok with papermill presents is awkward to use compared to a simple python function call). So, research software engineers like to refactor notebook workflows into Python modules, with clearly defined self-contained functions. These are unit-testable, easy to reuse in other contexts, and generally a known quantity, with best-in-class tooling.

When you have a Python module, debugging is in some parts better and in some parts worse compared to having the same code in a notebook:
* better: you can call a Python function and step into it with a debugger. If you call a notebook e.g. via papermill, you can't directly step into it with a debugger, you have to start a separate debugger, which is cumbersome and hard to reason about.
* better: you can generate data flow diagrams and call chains automatically, which helps in understanding code flow and debugging.
* worse: it is hard to whip up a quick, interactive plot at any place of the code during an interactive debugging session, e.g. in pdb or pudb. You can technically import matplotlib, set the right interactive backend (e.g. a Qt backend), and plot a dataframe, but this is cumbersome, doesn't work for everything (e.g. plotting with dash) and not nicely formatted. Also, iteration is hard, because you have to copy + paste + change the code for generating a plot instead of editing it directly in a cell like in a notebook.

This analysis shows that there is a clear gap: if we can enhance the debugging experience for scientific Python code in modules so that it as useful for debugging as the notebook flow, we could combine all the upsides of Python modules, without loosing the nice debuggability.

Requirements for a solution (let's call it judb):
* The user can set breakpoints both in the code via `breakpoint()` and interactively like in visual debuggers.
* The user can interactively step through code with the normal controls, step over, step into, continue until next breakpoint, step over line, etc. Like in visual debuggers.
* The user can also plot like in a notebook, with cells that can be executed at will and edited directly in the cell, where evaluation of the cell shows the result of the last expression in the cell under the cell. We can have multiple code cells, with multiple outputs.

Vague vision:
* Have a layout not unlike pudb, with the code + breakpoints in one pane, a variable viewer for simple variables like floats etc. in a second pane, a stack view in a third pane, and a interactive console in a fourth pane.
* The interactive console is a jupyter python console, with all bells and whistles, so with cells etc. like a jupyter notebook.
* This all runs in the browser, like a jupyter notebook. Some handling makes sure that once you hit the first breakpoint, a new page opens in your browser and you can start debugging.

Technology choices:
* If possible, we should look at how pudb does things. The backend can be very similar to pudb.
* For the frontend, we should look ad ipykernel and related technologies. We want to live in a browser and re-use as much as possible of the plumbing from those projects.
