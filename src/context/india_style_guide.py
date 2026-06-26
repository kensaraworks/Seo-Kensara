"""Indian English Style Guide enforcement — Module 2.8.B.

All substitutions are applied as a post-processing pass on generated text
to enforce Indian English spellings and remove banned Americanisms.

Full spec from module2_report.txt 2.8.B:
  - Mandatory spellings: American → Indian English
  - Banned Americanisms with correct replacements
  - Context-sensitive rules: practise (verb) / practice (noun)
                              licence (noun) / license (verb)
                              colour (for branding contexts only)
"""
import re

# Mandatory spellings: American → Indian English (Module 2.8.B)
SPELLING_SUBSTITUTIONS: list[tuple[str, str]] = [
    # Organisation spellings
    (r"\borganization\b", "organisation"),
    (r"\borganizations\b", "organisations"),
    (r"\borganizational\b", "organisational"),
    # Authorise/authorised
    (r"\bauthorized\b", "authorised"),
    (r"\bauthorize\b", "authorise"),
    (r"\bauthorization\b", "authorisation"),
    (r"\bunauthorized\b", "unauthorised"),
    # Recognise
    (r"\brecognized\b", "recognised"),
    (r"\brecognize\b", "recognise"),
    (r"\brecognizes\b", "recognises"),
    (r"\bunrecognized\b", "unrecognised"),
    # Programme — only when NOT "computer program" context
    (r"\bprogram\b(?!\s+(?:files|data|error|code|software|interface|language))", "programme"),
    (r"\bprograms\b(?!\s+(?:files|data|error|code|software|interface|language))", "programmes"),
    # Labour (HR/employment contexts only — safe to apply globally)
    (r"\blabor\b", "labour"),
    (r"\bfiber\b", "fibre"),
    (r"\bfibers\b", "fibres"),
    (r"\bcenter\b", "centre"),
    (r"\bcenters\b", "centres"),
    (r"\bcentered\b", "centred"),
    # Behaviour/behaviour
    (r"\bbehavior\b", "behaviour"),
    (r"\bbehaviors\b", "behaviours"),
    (r"\bbehavioral\b", "behavioural"),
    # Favour
    (r"\bfavor\b", "favour"),
    (r"\bfavors\b", "favours"),
    (r"\bfavorable\b", "favourable"),
    (r"\bfavored\b", "favoured"),
    # Colour — for branding contexts specifically
    (r"\bcolour\b", "colour"),  # already correct — enforce by blocking American
    (r"\bcolor\b(?=\s+(?:scheme|palette|brand|branding|theme|code|coding|coding|design))", "colour"),
    # Offence/Defence
    (r"\boffense\b", "offence"),
    (r"\boffenses\b", "offences"),
    (r"\bdefense\b", "defence"),
    (r"\bdefenses\b", "defences"),
    # Criticise/Summarise/Emphasise
    (r"\bcriticize\b", "criticise"),
    (r"\bemphasize\b", "emphasise"),
    (r"\bsummarize\b", "summarise"),
    # -ize → -ise (general high-frequency batch)
    (r"\banalyze\b", "analyse"),
    (r"\banalyzes\b", "analyses"),
    (r"\banalyzed\b", "analysed"),
    (r"\bminimize\b", "minimise"),
    (r"\bmaximize\b", "maximise"),
    (r"\boptimize\b", "optimise"),
    (r"\bcustomize\b", "customise"),
    (r"\bprioritize\b", "prioritise"),
    (r"\bfinalize\b", "finalise"),
    (r"\bsynchronize\b", "synchronise"),
    (r"\bmodernize\b", "modernise"),
    (r"\boperationalize\b", "implement"),
    (r"\boperationalized\b", "implemented"),
    (r"\boperationalizing\b", "implementing"),
    (r"\bstandardize\b", "standardise"),
    (r"\bcentralize\b", "centralise"),
    (r"\bdecentralize\b", "decentralise"),
    (r"\bnationalize\b", "nationalise"),
    (r"\bcharacterize\b", "characterise"),
    (r"\bcategorize\b", "categorise"),
    # Banned Americanisms from spec
    (r"\bmath\b", "maths"),
    (r"\bgotten\b", "got"),
    (r"\boftentimes\b", "often"),
]

# Banned Americanisms — Module 2.8.B (replace with cleaner alternatives)
# "leverage" as a verb is banned; as a noun (financial leverage) it is allowed.
BANNED_PHRASES: list[tuple[str, str]] = [
    (r"\bleveraging\b", "using"),
    (r"\bleveraged\b", "used"),
    (r"\bleverage\b(?=\s+(?:the|this|our|your|its|their|a|an|for|to|against|with))", "use"),
]

# Context-sensitive word pairs that require structural analysis before substitution.
# These cannot be blindly swapped — they are handled separately in apply_india_style().
#   practise (verb) / practice (noun) — critical for legal contexts
#   licence (noun) / license (verb)
PRACTICE_PATTERN = re.compile(
    r'\b(practice)\b(?=\s+(?:is|was|has|have|had|which|that|of|the|a|an|in|for|on|by|to|at|with))',
    re.IGNORECASE
)
PRACTISE_PATTERN = re.compile(
    r'\b(practice)\b(?=\s+(?:to|will|can|must|should|shall|may|would|could))',
    re.IGNORECASE
)
LICENCE_PATTERN = re.compile(
    r'\b(license)\b(?=\s+(?:is|was|has|have|had|which|that|of|the|a|an|in|for|on|by|at|with|agreement|holder|number|fee|period|term))',
    re.IGNORECASE
)
LICENSE_VERB_PATTERN = re.compile(
    r'\b(license)\b(?=\s+(?:to|will|can|must|should|shall|may|would|could))',
    re.IGNORECASE
)


def _preserve_case(original: str, replacement: str) -> str:
    """Preserve capitalisation of the original match in the replacement."""
    if original[0].isupper():
        return replacement[0].upper() + replacement[1:]
    return replacement


def apply_india_style(text: str) -> str:
    """Apply Indian English spelling and banned Americanism enforcement.

    Pass 1 — Spelling substitutions (American → Indian English)
    Pass 2 — Banned phrases
    Pass 3 — Context-sensitive noun/verb pairs (practise/practice, licence/license)

    Preserves capitalisation. Applied as a final post-processing pass
    before file write.
    """
    # Pass 1: Spelling substitutions
    for american, indian in SPELLING_SUBSTITUTIONS:
        def replace_match(m: re.Match, indian_word: str = indian) -> str:
            return _preserve_case(m.group(0), indian_word)
        text = re.sub(american, replace_match, text, flags=re.IGNORECASE)

    # Pass 2: Banned Americanisms
    for american_pattern, replacement in BANNED_PHRASES:
        text = re.sub(american_pattern, replacement, text, flags=re.IGNORECASE)

    # Pass 3: Context-sensitive noun/verb pairs
    # practise (verb) vs practice (noun) — noun: stays as 'practice'
    # We convert 'practice' used as a verb (followed by 'to', modal verbs)
    def _practise_replace(m: re.Match) -> str:
        return _preserve_case(m.group(0), "practise")

    text = PRACTISE_PATTERN.sub(_practise_replace, text)

    # licence (noun) vs license (verb)
    def _licence_replace(m: re.Match) -> str:
        return _preserve_case(m.group(0), "licence")

    text = LICENCE_PATTERN.sub(_licence_replace, text)

    return text


def audit_india_style(text: str) -> list[str]:
    """Return a list of violations found in the text without modifying it.

    Used by the QA checker (2.6) to identify Americanisms that are present
    in a post, so they can be flagged as issues rather than silently fixed.
    """
    violations = []
    for american, indian in SPELLING_SUBSTITUTIONS:
        matches = re.findall(american, text, flags=re.IGNORECASE)
        for match in matches:
            violations.append(f"Americanism detected: '{match}' → should be '{indian}'")
    for pattern, replacement in BANNED_PHRASES:
        matches = re.findall(pattern, text, flags=re.IGNORECASE)
        for match in matches:
            violations.append(f"Banned Americanism: '{match}' → use '{replacement}'")
    return violations
