# Manual Test Checklist: Forms UI

- Open the web app and start a room.
- Trigger a form (e.g., via tool or queued UI event) and verify:
  - Modal opens centered with dim backdrop.
  - Title and description render; description supports Markdown formatting.
  - Fields render correctly for types: text, textarea, number, date, email, url, select, multiselect, checkbox.
  - Required fields show an asterisk (configurable).
- Interactions:
  - Escape closes the modal (configurable).
  - Clicking outside closes the modal.
  - Enter submits when not focused in textarea (configurable).
  - Keyboard navigation works (Tab/Shift+Tab cycles inputs).
  - First input is auto-focused (configurable).
- Submission:
  - Submitting sends POST /api/forms/submit with correct payload.
  - On success, modal closes (configurable), and a toast appears (or assistant ack message when toasts disabled).
  - On failure, error toast displays.
- Settings integration:
  - Change values in config.yaml forms_ui (width, submit/cancel labels, toggles) and reload; verify behavior matches settings.

