# Coding Rules

## Do
- Write small classes and methods.
- Keep methods focused on a single responsibility.
- Prefer immutable objects.
- Use constructor injection for dependencies.
- Remove dead code and unused imports.
- Keep code readable and self-documenting.
- Prefer explicit code over "clever" code.
- Handle exceptions properly at the appropriate level.
- Use constants instead of magic numbers.
- **Mandatory Documentation:** 
    - Every class must include appropriate documentation (JSDoc/JavaDoc/Doxygen).
    - Every public method and interface must be documented.
    - Every parameter and return value must be described.
    - Document thrown exceptions.
    - Add usage examples for public APIs.

## Don't
- Do not duplicate logic (DRY principle).
- Do not use field/property injection unless required by the framework.
- Do not create unnecessary utility classes.
- Do not ignore or swallow exceptions.
- Do not create methods with multiple responsibilities.
- Do not over-engineer solutions.
