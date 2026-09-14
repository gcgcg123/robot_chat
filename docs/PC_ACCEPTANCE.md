# PC Phase 1 acceptance

1. Run `首次設定.bat` once. Set a Dashboard administrator password; the API key is optional because the dialogue service has a local fallback.
2. Run `scripts\provision-simulator.ps1 -DataDir "$env:LOCALAPPDATA\IoTGroup5" -DeviceId pc-sim` if heartbeat is enabled.
3. Double-click `一鍵啟動.bat`. The browser opens only after `/health` is ready.
4. Sign in with the administrator account and password.
5. Open **使用者列表 → 註冊新使用者**. Enter name, gender and age.
6. On the PC simulator, grant microphone permission and record three samples. The browser uses Web Audio to encode mono 16-bit PCM WAV (the backend accepts WAV only); raw samples are processed in memory and the stored template is an authenticated encrypted envelope.
7. Record a conversation turn. The no-RAG pipeline detects a coarse emotion, applies local risk rules, calls DeepSeek when configured, and falls back locally when it is unavailable. Captions and response segments are shown on the simulated 320x240 display.
8. Return to Dashboard and open the user row to inspect conversation, mood and risk summaries.

This checklist covers the laptop simulation only. ESP32-S3/LCD firmware, real MQTT broker, UDP audio and custom RAG content remain Phase 2/custom work.
