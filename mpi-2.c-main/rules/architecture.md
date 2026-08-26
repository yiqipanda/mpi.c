# Architecture Rules

Always follow the project's architecture.

## General Principles
- Follow Clean Architecture.
- Follow SOLID principles.
- Keep business logic inside the Domain layer.
- Keep framework-specific code inside the Infrastructure layer.
- Keep Controllers/Interfaces thin.
- Use dependency inversion.
- Prefer composition over inheritance.
- Keep modules loosely coupled.
- Keep classes focused on a single responsibility.

## Do
- Use clear boundaries between layers.
- Implement interfaces for external dependencies.
- Ensure all business rules are testable without infrastructure.

## Don't
- Do not place business logic inside Controllers or UI layers.
- Do not access the database directly from Controllers.
- Do not couple the Domain layer to specific frameworks or libraries.
- Do not create God classes.
- Do not mix infrastructure (DB, API clients) with business logic.

## Architecture Flow
The data flow should generally follow:
`Controller/UI -> Use Case (Application) -> Domain (Entity/Service) -> Repository Interface -> Infrastructure (Database/API implementation)`
