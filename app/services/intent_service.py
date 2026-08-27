import re
from typing import Optional, Dict, Any, Tuple

class IntentService:
    """
    Lightweight, deterministic rule-based intent classifier.
    Intercepts common speech patterns to bypass LLM calls for predictable turns.
    """

    CONFIRM_PATTERNS = [
        r"\b(yes|yeah|yep|sure|confirm|confirmed|that works|sounds good|correct|fine|ok|okay|haan|ha|bilkul|sahi|thik h|thik hai|avunu|sare)\b"
    ]

    CANCEL_PATTERNS = [
        r"\b(cancel|cancelled|cancellation|dont need|don't need|stop|drop|nahi|nako|oddu|koddhu|cancel kar do)\b"
    ]

    RESCHEDULE_PATTERNS = [
        r"\b(reschedule|change date|change time|another day|another time|next week|tomorrow|later|shift|postpone|badal do|marandi)\b"
    ]

    NOT_INTERESTED_PATTERNS = [
        r"\b(not interested|no thanks|dont call|don't call|wrong number|busy right now|dont want|naahi|oddu)\b"
    ]

    INTERESTED_PATTERNS = [
        r"\b(interested|tell me more|sounds good|details|explain|what is it|ha batao|cheppandi)\b"
    ]

    GOODBYE_PATTERNS = [
        r"\b(bye|goodbye|talk later|see you|thank you bye|no thanks bye|alvida|selavu)\b"
    ]

    def classify_intent(self, user_text: str, current_state: str) -> Tuple[Optional[str], Dict[str, Any]]:
        """
        Classifies user intent given transcript text and current conversation state.
        Returns (intent_name, extracted_vars).
        """
        if not user_text:
            return None, {}

        text_clean = user_text.strip().lower()
        extracted_vars = {}

        # 1. State-Specific Name Extraction Guardrail
        if current_state in ("GREETING", "WAIT_FOR_NAME", "ASK_NAME"):
            name = self.extract_name(user_text)
            if name:
                extracted_vars["customer_name"] = name
                return "NAME_PROVIDED", extracted_vars

        # 2. Check Goodbye
        for pat in self.GOODBYE_PATTERNS:
            if re.search(pat, text_clean):
                return "GOODBYE", extracted_vars

        # 3. Check Cancel
        for pat in self.CANCEL_PATTERNS:
            if re.search(pat, text_clean):
                return "CANCEL", extracted_vars

        # 4. Check Reschedule
        for pat in self.RESCHEDULE_PATTERNS:
            if re.search(pat, text_clean):
                return "RESCHEDULE", extracted_vars

        # 5. Check Confirm / Yes
        for pat in self.CONFIRM_PATTERNS:
            if re.search(pat, text_clean):
                return "CONFIRM", extracted_vars

        # 6. Check Not Interested
        for pat in self.NOT_INTERESTED_PATTERNS:
            if re.search(pat, text_clean):
                return "NOT_INTERESTED", extracted_vars

        # 7. Check Interested
        for pat in self.INTERESTED_PATTERNS:
            if re.search(pat, text_clean):
                return "INTERESTED", extracted_vars

        return None, extracted_vars

    def extract_name(self, text: str) -> Optional[str]:
        """
        Extracts customer name from introduction phrases or direct name utterances.
        Handles: 'Akash', 'I'm Akash', 'I am Akash', 'My name is Akash',
                 'This is Akash', 'Akash here', 'Speaking, Akash',
                 'Hello Akash', 'Hi I'm Akash', 'It's Akash', 'Myself Akash'.
        If multiple valid name candidates are found in the utterance (e.g. 'I am Vaibhav or Arjun'),
        returns 'MULTIPLE_NAMES_REJECTED' so the controller asks for clarification.
        """
        if not text:
            return None

        # Clean trailing punctuation and normalize
        t = text.strip().rstrip(".,!?")

        # Check for multiple candidate names in the input text (prevents extracting names from hallucinated list)
        words_in_text = re.findall(r"[A-Za-z\u0900-\u097F\u0C00-\u0C7F]+", t)
        stop_words = {
            "i", "i'm", "my", "name", "is", "this", "it's", "it", "myself", "am", "called", "speaking",
            "here", "hello", "hi", "hey", "yes", "no", "a", "an", "the", "and", "or",
            "मेरा", "नाम", "है", "हूँ", "मैं", "जी", "नमस्ते", "हाँ"
        }
        candidate_words = [w.title() for w in words_in_text if w.lower() not in stop_words]
        if len(set(candidate_words)) > 2:
            # Multiple distinct candidate names detected (hallucination or confused input)
            return "MULTIPLE_NAMES_REJECTED"

        # Pattern 1: Explicit introduction phrases (EN + HI)
        # "I'm / My name is / मेरा नाम / मैं / Naam" <name>
        match = re.search(
            r"\b(?:i'm|i am|my name is|this is|it's|it is|myself|naam|naam hai|i am called|call me|speaking|मेरा नाम|मैं|नाम)\s+([A-Za-z\u0900-\u097F\u0C00-\u0C7F]+(?:\s+[A-Za-z\u0900-\u097F\u0C00-\u0C7F]+)?)",
            t, re.IGNORECASE
        )
        if match:
            candidate = match.group(1).strip().title()
            # Remove trailing Hindi copula if matched (e.g. "वैभव है" -> "वैभव")
            candidate_words = [w for w in candidate.split() if w.lower() not in ("है", "हूँ", "is", "am")]
            if candidate_words:
                first_name = candidate_words[0]
                if self._is_valid_name(first_name):
                    return first_name

        # Pattern 2: "Hello/Hi/Hey/नमस्ते, <name>"
        match = re.search(
            r"\b(?:hello|hi|hey|नमस्ते),?\s+(?:i'm\s+|मैं\s+)?([A-Za-z\u0900-\u097F\u0C00-\u0C7F]+)",
            t, re.IGNORECASE
        )
        if match:
            candidate = match.group(1).strip().title()
            if self._is_valid_name(candidate):
                return candidate

        # Pattern 3: "<name> here" or "<name> speaking" or "Speaking, <name>"
        match = re.search(
            r"([A-Za-z\u0900-\u097F\u0C00-\u0C7F]+)\s+(?:here|speaking|this side|बोल रहा|बोल रही)",
            t, re.IGNORECASE
        )
        if match:
            candidate = match.group(1).strip().title()
            if self._is_valid_name(candidate):
                return candidate

        match = re.search(
            r"(?:speaking|yes|हाँ),?\s+([A-Za-z\u0900-\u097F\u0C00-\u0C7F]+)",
            t, re.IGNORECASE
        )
        if match:
            candidate = match.group(1).strip().title()
            if self._is_valid_name(candidate):
                return candidate

        # Pattern 4: Single word or two-word utterance that is likely a name
        words = [w for w in t.split() if w.lower() not in ("है", "हूँ", "is", "am")]
        if len(words) == 1:
            candidate = words[0].title()
            if self._is_valid_name(candidate):
                return candidate
        elif len(words) == 2:
            # "Akash Sharma" — return first name
            candidate = words[0].title()
            if self._is_valid_name(candidate):
                return candidate

        return None

    def _is_valid_name(self, name: str) -> bool:
        """Reject obvious non-names (EN + HI)."""
        INVALID_WORDS = {
            "unknown", "none", "null", "undefined", "n/a", "user", "customer",
            "hello", "hi", "hey", "yes", "no", "ok", "okay", "thanks", "thank",
            "yeah", "bye", "goodbye", "sorry", "please", "sure", "great", "fine",
            "good", "right", "well", "hmm", "uh", "um", "ah", "oh", "er",
            "speaking", "here", "this", "that", "it", "is", "am", "are", "my",
            "name", "call", "i", "me", "we", "you", "he", "she", "they",
            "behind", "beyond", "tomorrow", "hospital", "confirm", "cancel",
            "reschedule", "appointment", "home", "from", "dont", "don't", "know",
            "think", "what", "where", "when", "why", "how", "who", "which",
            "because", "just", "maybe", "probably", "nothing", "anything",
            "something", "everything", "day", "today", "morning", "afternoon",
            "evening", "night", "doctor", "clinic", "time", "date", "slot",
            "schedule", "number", "phone", "message", "work", "school", "car",
            "bus", "train", "city", "street", "road", "place", "location",
            "skyline", "citycare", "developers", "property", "apartments", "bhk",
            # Hindi invalid words
            "मेरा", "नाम", "है", "हूँ", "नमस्ते", "हाँ", "जी", "बात", "कर", "रहा", "रही",
            "कॉल", "अपॉइंटमेंट", "हॉस्पिटल", "डॉक्टर", "कैंसिल", "कन्फर्म", "रीशेड्यूल",
            # AI persona names must not be accepted as customer names
            "sophia", "maya", "ananya", "arjun", "david",
        }
        n = name.strip()
        if not n or len(n) < 2:
            return False
        if n.lower() in INVALID_WORDS:
            return False
        # Must contain at least one alphabetic character from a supported script
        if not re.search(r"[A-Za-z\u0900-\u097F\u0C00-\u0C7F]", n):
            return False
        # Reject purely numeric strings
        if n.isdigit():
            return False
        return True

