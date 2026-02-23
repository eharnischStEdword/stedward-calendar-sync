# Documentation Index

## Quick Links

- **[System Architecture](architecture/system-overview.md)** - How the system works
- **[Deployment Guide](guides/deployment.md)** - How to deploy and configure
- **[Troubleshooting](guides/troubleshooting.md)** - Common issues and solutions
- **[Testing](../tests/README.md)** - Test suite documentation
- **[Main README](../README.md)** - Project overview

## Architecture Documentation

- [System Overview](architecture/system-overview.md) - Components and data flow

## Guides

- [Deployment](guides/deployment.md) - Deployment procedures
- [Adding dashboard users](guides/adding-dashboard-users.md) - Add users (e.g. ckloss@stedward.org) for sign-in and event search
- [Troubleshooting](guides/troubleshooting.md) - Problem diagnosis and solutions

## Historical Documentation

Documentation of past fixes and changes:

- [Duplicate Fix (2024)](historical/duplicate-fix-2024.md) - Duplicate event prevention implementation
- [Signature Fix (2024)](historical/signature-fix-2024.md) - Signature consistency fix
- [Multi-Day Event Fix (2025)](historical/multiday-fix-2025.md) - Multi-day event duplication fix
- [Background Sync Implementation](historical/background-sync-implementation.md) - Worker timeout fix and background sync implementation
- [System Status Summary](historical/system-status-summary.md) - System status and behavior documentation

**Note:** Historical docs are archived for reference. Implementation details may have changed since these were written.

## Contributing

When adding documentation:

1. **Architecture changes** → Update `architecture/system-overview.md`
2. **New procedures** → Add to `guides/` directory
3. **Historical fixes** → Archive in `historical/` directory
4. **Update this index** → Add links to new documentation

## Documentation Standards

- Use Markdown format
- Include code examples where relevant
- Keep diagrams/tables simple and readable
- Update documentation with code changes
- Archive historical documentation, don't delete
