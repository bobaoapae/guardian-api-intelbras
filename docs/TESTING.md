# Testing Guide

**Status**: Coming soon in FASE 2

This guide will explain how to test the integration with a real Intelbras alarm system.

## Topics to Cover

- Setting up test environment
- Creating test credentials
- Running integration tests with real API
- Testing arm/disarm operations safely
- Validating API response structures
- Documenting differences from expected responses

## Safety Warning

Testing will involve real alarm operations. Always ensure:
- You have physical access to the alarm system
- You can disarm the alarm quickly if needed
- Tests are run during appropriate hours
- Family/neighbors are aware of testing

## Test Scenarios

Will include:
1. Authentication flow
2. Device discovery
3. Partition status checks
4. Arm/disarm operations
5. Zone status monitoring
6. Event retrieval
