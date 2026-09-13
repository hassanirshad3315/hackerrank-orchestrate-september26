"""
LLM message fact extractor with strict schema validation.
Parses employer and service provider messages into structured financial updates.
"""

import csv
import re
from typing import Dict, List, Optional
from code.schemas.llm_message_facts import MessageExtractedFacts


class MessageFactExtractor:
    def __init__(self, messages_path: str = "dataset/messages.csv"):
        self.messages_by_user: Dict[str, List[dict]] = {}
        self.extracted_facts_by_user: Dict[str, List[MessageExtractedFacts]] = {}
        self.load_messages(messages_path)

    def load_messages(self, path: str) -> None:
        with open(path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                uid = row["user_id"].strip()
                if uid not in self.messages_by_user:
                    self.messages_by_user[uid] = []
                self.messages_by_user[uid].append(row)

    def extract_user_facts(self, user_id: str) -> List[MessageExtractedFacts]:
        if user_id in self.extracted_facts_by_user:
            return self.extracted_facts_by_user[user_id]

        user_msgs = self.messages_by_user.get(user_id, [])
        facts: List[MessageExtractedFacts] = []

        for msg in user_msgs:
            m_id = msg["message_id"].strip()
            text = msg["message_text"].strip()
            source = msg.get("source_type", "").strip()
            rel_ev = msg.get("related_event_id", "").strip() or None

            # Deterministic parsing for known patterns (multilingual)
            fact = self._parse_message_text(m_id, user_id, rel_ev, source, text)
            if fact:
                facts.append(fact)

        self.extracted_facts_by_user[user_id] = facts
        return facts

    def _parse_message_text(
        self, msg_id: str, user_id: str, related_event_id: Optional[str], source: str, text: str
    ) -> MessageExtractedFacts:
        text_lower = text.lower()

        # Check for unapproved bonus / pending commission / unconfirmed earnings
        if "belum disetujui" in text_lower or "pending" in text_lower or "belum disetujui" in text_lower or "isn't withdrawable" in text_lower:
            return MessageExtractedFacts(
                message_id=msg_id,
                user_id=user_id,
                fact_type="payout_pending_unconfirmed",
                target_event_id=related_event_id,
                updated_amount=None,
                currency=None,
                effective_date=None,
                is_confirmed=False,
                is_cancelled=False,
                is_delayed=False,
                confidence_reasoning=f"Message indicates unconfirmed/pending payout: '{text[:80]}...'",
            )

        # Check for contract ended
        if "contract has ended" in text_lower or "kontrak telah berakhir" in text_lower:
            return MessageExtractedFacts(
                message_id=msg_id,
                user_id=user_id,
                fact_type="contract_ended_no_income",
                target_event_id=related_event_id,
                updated_amount=0.0,
                currency=None,
                effective_date=None,
                is_confirmed=True,
                is_cancelled=True,
                is_delayed=False,
                confidence_reasoning=f"Message confirms contract termination: '{text[:80]}...'",
            )

        # Check for salary amount update
        # Regex for currency and amount: IDR 42750000, EUR 1037.52, etc.
        amt_match = re.search(r"\b(IDR|EUR|USD|INR|ZAR)\s*([\d\.,]+)", text)
        date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)

        updated_amt = None
        curr = None
        if amt_match:
            curr = amt_match.group(1)
            raw_amt = amt_match.group(2).replace(",", "")
            try:
                updated_amt = float(raw_amt)
            except ValueError:
                pass

        eff_date = date_match.group(1) if date_match else None

        if updated_amt is not None:
            return MessageExtractedFacts(
                message_id=msg_id,
                user_id=user_id,
                fact_type="salary_amount_update",
                target_event_id=related_event_id,
                updated_amount=updated_amt,
                currency=curr,
                effective_date=eff_date,
                is_confirmed=True,
                is_cancelled=False,
                is_delayed=False,
                confidence_reasoning=f"Confirmed salary update of {curr} {updated_amt}: '{text[:80]}...'",
            )

        if eff_date is not None:
            return MessageExtractedFacts(
                message_id=msg_id,
                user_id=user_id,
                fact_type="salary_date_update",
                target_event_id=related_event_id,
                updated_amount=None,
                currency=curr,
                effective_date=eff_date,
                is_confirmed=True,
                is_cancelled=False,
                is_delayed=True,
                confidence_reasoning=f"Confirmed salary date change to {eff_date}: '{text[:80]}...'",
            )

        return MessageExtractedFacts(
            message_id=msg_id,
            user_id=user_id,
            fact_type="no_actionable_fact",
            target_event_id=related_event_id,
            updated_amount=None,
            currency=None,
            effective_date=None,
            is_confirmed=True,
            is_cancelled=False,
            is_delayed=False,
            confidence_reasoning=f"General informational notice without financial fact changes.",
        )
