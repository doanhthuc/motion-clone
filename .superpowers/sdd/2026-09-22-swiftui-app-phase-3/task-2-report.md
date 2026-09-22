# Task 2 Report: Authenticated Patch and JSON Delete

## Implementation

- Added `APIClient.patch(_:body:_:)` in `ios/MotionKit/Sources/MotionKit/API/APIClient.swift`.
  - Encodes the `Encodable` body with `JSONEncoder.keyEncodingStrategy = .convertToSnakeCase`.
  - Sends `PATCH` with `Content-Type: application/json` through the existing authenticated `send` helper.
  - Accepts HTTP `200` and decodes through the existing `decode` helper.
  - Maps encoding failures to `APIError.transport` using the same policy as JSON `POST`.
- Added the decoded `APIClient.delete(_:_:) -> Response` overload.
  - Sends authenticated `DELETE`, accepts HTTP `200`, and decodes the response.
- Preserved the existing bodyless `delete(_ components:)` overload unchanged; it continues to accept only HTTP `204`.
- Added transport tests for snake-case patch JSON/auth headers, explicit JSON `null` slots, and decoded `200` delete responses with encoded path segments.

## TDD RED/GREEN

RED command:

```text
cd ios/MotionKit && swift test --filter APIClientTests
```

Result: failed at compilation as expected because `APIClient` had no `patch` member and no decoded `delete` overload.

GREEN command:

```text
cd ios/MotionKit && swift test --filter APIClientTests
```

Result: passed; 20 tests in 2 suites passed.

## Final verification

```text
cd ios/MotionKit && swift test
```

Result: passed; 66 tests in 11 suites passed.

```text
motions-studio/setup/scrub-secrets.sh --check
```

Result: passed; source reported clean with no known secrets or personal emails.

## Self-review

- Diff is limited to the requested API client and API client test files, plus this report.
- The patch method reuses the established JSON POST encoding and authenticated transport behavior.
- The decoded delete method has an explicit `200` status set and cannot alter the existing bodyless `204` behavior.
- Tests verify method, content type, bearer authorization, snake-case payload behavior, explicit null preservation, response decoding, and path escaping.
- `git diff --check` passed.

## Concerns

None identified within the requested scope. No live service or iOS simulator verification was needed for these pure MotionKit transport primitives.

## Fix Round 1

- Changed `ios/MotionKit/Tests/MotionKitTests/APIClientTests.swift` only.
- Added the test-only `CamelCasePatchBody` payload with a `materialID` property.
- Added `patchConvertsCamelCaseBodyKeysToSnakeCase`, which asserts `material_id` is emitted and `materialID` is absent. This directly proves the PATCH encoder's snake-case strategy.
- Command: `cd ios/MotionKit && swift test --filter APIClientTests`
- Output: passed; 21 tests in 2 suites passed.
