# ESP 到貨後接入步驟

目前 Dashboard 使用 simulator heartbeat，不能視為 ESP 真機已連線。硬體到貨後按以下順序：

1. ESP 與 laptop 連到同一個區域網路。
2. 啟動上游 xiaozhi WebSocket server（預設 8000）及 OTA/HTTP（預設 8003）。
3. 啟動 MQTT gateway（1883/TCP、8884/UDP、8007/API）。
4. OTA URL 使用 laptop 的 LAN IP，不能使用 `localhost`。
5. 驗證 ESP heartbeat 出現在 Dashboard 的裝置清單。
6. 驗證 MQTT 指令、UDP 音訊、Whisper ASR、DeepSeek 回覆與 Dashboard 對話事件。

真機驗證前，保留 simulator 作為回歸測試來源。
