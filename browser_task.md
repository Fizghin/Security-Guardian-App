
# Browser E2E Test Task

1.  **Navigate** to `http://localhost:2500`.
2.  **Verify Loading**: Wait for the "ONLINE" status or the video feed container to appear.
3.  **Monitor Stream**:
    -   Wait 5 seconds.
    -   Check if the FPS counter (if visible) is updating (> 0).
    -   Wait another 10 seconds.
    -   Verify the page has not crashed/frozen (e.g., buttons are still clickable).
4.  **Test Panic Button**:
    -   Click the "PANIC BUTTON".
    -   Handle any browser alerts (accept them).
5.  **Test Update Config**:
    -   Click "UPDATE PARAMETERS".
    -   Handle any alerts.
6.  **Report**:
    -   Did the video stay active?
    -   Did the buttons work?
