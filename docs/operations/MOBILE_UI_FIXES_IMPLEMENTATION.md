# Mobile UI Fixes Implementation

**Date:** October 15, 2025
**Status:** Completed

## Overview

This document describes the implementation of quality-of-life fixes for the mobile UI, specifically addressing issues with keyboard interaction, composer height, and scroll behavior when typing messages on mobile devices.

## Issues Addressed

### 1. Excessive Space Consumption
**Problem:** When clicking inside the message box on mobile, the composer and keyboard combined consumed most of the viewport, making it difficult to see messages.

**Solution:** Reduced the maximum composer height from 120px to 80px when the keyboard is active on mobile, and added CSS constraints to prevent excessive expansion.

### 2. Over-Scrolling Beyond Last Message
**Problem:** When clicking inside the message box, the page would scroll down extra far beyond the most recent message, hiding Theo's reply.

**Solution:** Implemented input focus detection that prevents auto-scrolling for 800ms after the input is focused. This gives the keyboard time to animate in without triggering aggressive scroll behavior.

### 3. Message Section Not Appearing
**Problem:** On first open after closing the app, when clicking inside the message box, the keyboard would appear but the message section wouldn't show properly.

**Solution:** Improved keyboard detection timing and viewport height locking to prevent layout shifts, ensuring the message section remains visible during keyboard transitions.

## Technical Implementation

### File: `web/src/ui.ts`

**Changes:**
- Added `notifyInputFocus()` function to track when input elements are focused
- Modified `scrollMessagesToBottom()` to skip auto-scroll for 800ms after input focus
- Exposed `notifyInputFocus` on window object for cross-component access

**Key Code:**
```typescript
// Track when input is being focused to prevent aggressive scroll behavior
let lastInputFocusTime = 0;
export function notifyInputFocus() {
  lastInputFocusTime = Date.now();
}

// In scrollMessagesToBottom:
const timeSinceFocus = Date.now() - lastInputFocusTime;
if (!force && timeSinceFocus < 800) {
  // Input was just focused, likely keyboard is still animating - skip this scroll
  return;
}
```

### File: `web/src/App.tsx`

**Changes:**
- Enhanced `handleFocusIn` to call `notifyInputFocus()` when textarea/input is focused
- Added immediate keyboard detection by setting `mobile-keyboard-active` class on focus
- Improved timing of keyboard state checks

**Key Code:**
```typescript
const handleFocusIn = (e: FocusEvent) => {
  const target = e.target as HTMLElement;
  if (target.tagName === 'TEXTAREA' || target.tagName === 'INPUT') {
    // Notify the UI system that input is being focused (prevents aggressive scrolling)
    try {
      if ((window as any).notifyInputFocus) {
        (window as any).notifyInputFocus();
      }
    } catch {}

    // Immediately mark keyboard as potentially active to adjust UI
    document.documentElement.classList.add('mobile-keyboard-active');
    
    // Continue with resize detection...
  }
};
```

### File: `web/src/components/Composer.tsx`

**Changes:**
- Reduced max height from 120px to 80px when keyboard is active on mobile
- Added `onFocus` handler to textarea to call `notifyInputFocus()`

**Key Code:**
```typescript
const maxH = isMobileScreen()
  ? (isKeyboardActive ? 80 : 220) // Significantly reduced from 120 to 80
  : 320;

// In textarea props:
onFocus: () => { 
  try { 
    if ((window as any).notifyInputFocus) 
      (window as any).notifyInputFocus(); 
  } catch {} 
}
```

### File: `web/src/styles/layoutStyles.ts`

**Changes:**
- Fixed body position on mobile to prevent unwanted scrolling
- Added `flex-shrink: 0` to composer to prevent it from being compressed
- Added max-height constraints when keyboard is active
- Improved messages container min-height when keyboard is active

**Key CSS:**
```css
@media (max-width: 800px) {
  body {
    overflow: hidden;
    position: fixed;
    width: 100%;
    height: 100vh;
    height: 100dvh;
  }
  
  html.mobile-keyboard-active body {
    position: fixed;
    width: 100%;
    height: 100vh;
  }
  
  .composer {
    flex-shrink: 0;
  }
  
  html.mobile-keyboard-active .composer {
    max-height: 30vh;
    overflow-y: auto;
  }
  
  html.mobile-keyboard-active .composer textarea {
    max-height: 80px;
  }
  
  html.mobile-keyboard-active #messagesInner {
    flex: 1 1 auto;
    min-height: 200px;
  }
}
```

## How It Works

### Flow of Events

1. **User taps message input on mobile:**
   - `handleFocusIn` in App.tsx is triggered
   - `notifyInputFocus()` is called, recording timestamp
   - `mobile-keyboard-active` class is added to `<html>`
   - Composer height is immediately constrained to 80px max

2. **Keyboard animation begins:**
   - Viewport height changes detected via resize events
   - `checkKeyboardState()` validates keyboard is truly active
   - Any scroll attempts are blocked for 800ms via `scrollMessagesToBottom` check

3. **User starts typing:**
   - Composer auto-grows but stays within 80px limit
   - Messages container maintains minimum 200px height
   - Scroll behavior returns to normal after 800ms

4. **User finishes and blurs input:**
   - `handleFocusOut` removes `mobile-keyboard-active` class after 200ms delay
   - Composer returns to normal 220px max height
   - Full viewport height is restored

### Timing Rationale

- **800ms scroll block:** Allows for keyboard animation (typically 300-500ms) plus a buffer for viewport adjustments
- **200ms blur delay:** Prevents flickering when focus shifts between inputs
- **150ms keyboard check delay:** Gives viewport time to resize before validation

## Testing Recommendations

### Manual Testing on Mobile Device

1. **First-time focus after app open:**
   - Open app
   - Tap message input
   - Verify: Keyboard appears, messages stay visible, no over-scroll
   - Verify: Can see at least the last 2-3 messages

2. **Composer height:**
   - Tap message input
   - Type a short message (1 line)
   - Verify: Composer stays compact
   - Type a long message (multiple lines)
   - Verify: Composer grows but stops at ~80px, adds scrollbar if needed

3. **Scroll behavior:**
   - Have a conversation with Theo
   - While viewing old messages, tap input
   - Verify: View doesn't jump to bottom automatically
   - Send a message
   - Verify: View scrolls to show new message after send

4. **Keyboard transitions:**
   - Tap input, then tap a different input field
   - Verify: No flashing or layout jumps
   - Tap input, then tap outside to dismiss keyboard
   - Verify: Smooth transition back to full view

### iOS Specific Testing

- Test with Safari (iOS keyboard behavior differs)
- Test with safe area insets (notched devices)
- Test portrait and landscape orientations

### Android Specific Testing

- Test with Chrome (different keyboard animation timing)
- Test with gesture navigation vs 3-button navigation
- Verify different keyboard heights are handled

## Potential Future Enhancements

1. **Dynamic height adjustment:** Could measure actual keyboard height and adjust composer proportionally
2. **Smart scroll positioning:** Instead of blocking scroll entirely, could scroll to show last message + input
3. **Persistent scroll position:** Remember user's scroll position when keyboard opens/closes
4. **Accessibility improvements:** Add announcements for screen readers when keyboard state changes

## Rollback Procedure

If issues arise, revert changes to these files:
1. `web/src/ui.ts` - Remove `notifyInputFocus` function and 800ms check
2. `web/src/App.tsx` - Remove `notifyInputFocus()` call from `handleFocusIn`
3. `web/src/components/Composer.tsx` - Change 80px back to 120px, remove onFocus handler
4. `web/src/styles/layoutStyles.ts` - Remove mobile-keyboard-active CSS rules

## Related Documentation

- `/docs/specs.md` - Overall project specifications
- `/web/src/__tests__/composer_mobile_enter.spec.tsx` - Mobile composer tests
- `/web/src/__tests__/composer_send_mobile_render.spec.tsx` - Mobile rendering tests

## Author Notes

The key insight for these fixes was recognizing that the scroll behavior needed to be suppressed **during** keyboard animation, not just prevented entirely. The 800ms window allows the mobile browser to complete its layout adjustments before normal scrolling behavior resumes.

The height reduction from 120px to 80px was chosen based on the average mobile viewport height (typically 600-800px) minus keyboard (250-350px), minus header (60px), leaving ~200-300px for messages. An 80px composer leaves sufficient room for messages while still being usable.

