"""Reviewed literal aliases with bounded negation/contradiction guards.

Opt-in per fact. This is still a proxy, not a semantic judge; do not globally
rewrite Chinese negations or accept any answer containing the word '不'.
"""
import re


_REVERSED = re.compile(r"(?:并非|不是|不能说|不能认为|不代表|并不意味着|否认).{0,4}$")
_NEGATED = re.compile(r"(?:不|未|没有|并非|不是|不能|无法|不得|不应|未能|不代表|不意味着|不能说|不能认为|错误说法(?:是)?)$")
_REMEDIATION_CONDITION = re.compile(
    r'(?:^|[。；;，,！？!?\n])(?:如果|假如|倘若|一旦|若)'
    r'[^。；;，,！？!?\n]{1,48}?(?:[，,])?(?:则|就)?'
    r'(?:应当|应该|必须|需要|应|须|需|要)(?:进行|及时|立即)?'
    r'(?:整改|复核|重新核验|重新验证|升级处理|通知负责人|发出告警)'
    r'(?=$|[。；;，,！？!?\n])')


def _occurrences(text, phrase):
    start = 0
    while phrase and (index := text.find(phrase, start)) >= 0:
        yield index
        start = index + len(phrase)


def negative_assertion_match(text, aliases, rule):
    return fact_assertion_match(text, aliases, rule)


def fact_assertion_match(text, aliases, rule):
    if rule.get('polarity') not in ('negative', 'positive'):
        raise ValueError('Unsupported fact assertion polarity')
    # Punctuation boundaries keep unrelated earlier negations from changing this clause.
    def prefix(index):
        return re.split(r'[。；;，,！？!?\n]|但是|然而|但|却|实际上', text[max(0, index - 24):index])[-1]

    # Only explicitly enabled, bounded remediation instructions are hypothetical.
    # Ignore them both as positive evidence and as contradictions. Do not discard
    # arbitrary clauses containing "如果" or a current factual failure nearby.
    conditional_spans = ([match.span() for match in _REMEDIATION_CONDITION.finditer(text)]
                         if rule.get('ignore_remediation_conditionals') else [])
    def hypothetical(index):
        return any(start <= index < end for start, end in conditional_spans)

    guard = _REVERSED if rule['polarity'] == 'negative' else re.compile(
        r'(?:不|未|没有|不会|不能|无法|不得|不应)$|(?:并非|不是|不能说|不能认为|不代表|并不意味着|否认'
        + (r'|如果|假如|倘若|一旦|否则|若' if rule.get('ignore_remediation_conditionals') else '')
        + r').{0,16}$')
    accepted = any(not hypothetical(index) and not guard.search(prefix(index))
                   for alias in aliases for index in _occurrences(text, alias))
    contradictions = [phrase for phrase in rule.get('contradictions', [])
                      if any(not hypothetical(index) and not _NEGATED.search(prefix(index))
                             for index in _occurrences(text, phrase))]
    return accepted and not contradictions, contradictions
