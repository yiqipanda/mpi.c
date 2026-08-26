# Project Structure

This project follows **Clean Architecture** principles to ensure maintainability, scalability, and testability.

## Directory Structure

```text
/
├── .rules/             # AI & Development rules and standards
├── docs/               # Project documentation
│   └── reports/        # Task completion reports (AI generated)
├── src/                # Source code
│   ├── Domain/         # Enterprise business rules (Entities, Interfaces)
│   ├── Application/    # Application business rules (Use Cases)
│   ├── Infrastructure/ # Frameworks, Drivers, DB, External APIs
│   └── Interface/      # Controllers, Presenters, UI
├── tests/              # Test suites (Unit, Integration, E2E)
├── roadmap.md          # Project milestones and progress
└── README.md           # Project overview
```

## Layers

1.  **Domain Layer:** The heart of the application. Contains entities, value objects, and repository interfaces. No dependencies on other layers.
2.  **Application Layer:** Contains use cases that orchestrate the flow of data to and from the entities.
3.  **Infrastructure Layer:** Implementation of database repositories, file system access, or third-party service clients.
4.  **Interface Layer:** Entry points to the application (HTTP Controllers, CLI commands).
