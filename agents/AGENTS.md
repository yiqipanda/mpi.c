# Set of Instructions for Agents

*Current scope: temp_src/ & scripts/ and all of it's contents. execute run.sh  to actually test the system.*

Distinguish what user actually asks for, if they require a method or function look to relevant section and skip otherwise.

## Method & Function Implementations

Do not begin modifying or writing the method immediately. First, consider whether the method may already be implemented and whether the actual task is simply to determine where it should be called.

Before implementation begins, explain the method's expected behavioral impact, where it will be used, what state it may mutate, what external effects it may cause, and any relevant implementation constraints. At this stage, you may inspect existing code and reason about integration points, but you should not yet modify the method or add its implementation.

Implementation begins only after the user responds to this behavioral analysis by either accepting it or providing additional details that solidify the intended behavior. Once that response is received, you may proceed with writing or modifying the method.

For all methods, these criteria must be checked:

1. Type annotations for input and output.
2. The method's main goal must be preserved and not skewed.
3. Global variables and system calls used inside the function must all be explicitly annotated to the user.
4. Avoid triple nesting whenever possible. If it is not avoidable, temporarily construct a helper function.
5. All helper functions for the method must be annotated to the user, and helper functions must be referentially transparent.
6. Avoid adding new class data fields when implementing a method, but verbosely explain the requirement after the method is implemented in the portal.
7. Some method implementations can take multiple user-agent exchanges. If this document is not helpful in completing the user's goal after several trials, dismiss this document.
8. When you see that the method is properly implemented according to the set of criteria, provide a brief summary proving that each criterion has been checked, then terminate.
