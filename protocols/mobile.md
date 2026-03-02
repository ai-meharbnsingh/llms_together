# Mobile Development Protocol

## Stack
- Framework: React Native (TypeScript) or Flutter (Dart)
- Backend: Python (FastAPI) or Node.js — shared API with web if applicable
- Database: SQLite (local) + PostgreSQL (remote)
- State: Redux/Zustand (RN) or Riverpod/BLoC (Flutter)

## Architecture Rules
- Offline-first: all critical features work without network
- Local DB (SQLite/Realm) syncs to remote when connected
- Conflict resolution: last-write-wins with timestamp, or queue-based
- Background sync: periodic + on-reconnect
- Push notifications: FCM (Android) + APNs (iOS)

## UI/UX Rules
- Platform-specific design: Material Design (Android), Human Interface (iOS)
- Safe area insets: respect notch, home indicator, status bar
- Touch targets: minimum 44x44pt (iOS) / 48x48dp (Android)
- Navigation: stack-based with tab bar (max 5 tabs)
- Loading states: skeleton screens (not spinners)
- Error states: retry button + offline indicator
- Haptic feedback on key interactions

## Performance Rules
- App launch: < 2 seconds to interactive
- Screen transitions: < 300ms
- Image loading: progressive + cached
- List rendering: virtualized (FlatList/ListView)
- Memory: monitor for leaks, < 200MB peak
- Bundle size: < 50MB initial download

## Offline-First Data
- Queue all mutations when offline
- Show pending state in UI (sync indicator)
- Resolve conflicts on reconnect
- Local cache with TTL for read data
- Secure storage for tokens/secrets (Keychain/Keystore)

## Security
- Certificate pinning for API calls
- Biometric auth option (Face ID / Fingerprint)
- Secure storage for sensitive data (never AsyncStorage for tokens)
- Root/jailbreak detection
- Code obfuscation in production builds
- No sensitive data in logs

## Testing
- Unit: business logic, state management
- Widget/Component: UI rendering, interaction
- Integration: API calls, offline queue, sync
- E2E: Detox (RN) or integration_test (Flutter)
- Device matrix: test on min 3 screen sizes

## Build & Distribution
- CI/CD: Fastlane for both platforms
- Code signing: automated via CI
- Beta: TestFlight (iOS) + Firebase App Distribution (Android)
- Versioning: semver with build number auto-increment

## Filesystem Access
Workers, Kimi, and the Orchestrator have FULL filesystem access to the project folder.
This includes: creating/editing/deleting files, running builds, installing pods/gradle,
running emulators, executing Fastlane, and any operational task.
NO human permission required for operational actions.
Human involvement ONLY for: platform decisions, UX changes, and escalations.
