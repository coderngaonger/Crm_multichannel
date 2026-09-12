import unicodedata


def strip_accents(text: str) -> str:
    """Vietnamese customers frequently type without diacritics ('hoan tien'
    vs 'hoàn tiền'). Normalising both sides keeps keyword matching working
    for either style."""
    decomposed = unicodedata.normalize("NFD", text)
    without_marks = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return without_marks.replace("đ", "d").replace("Đ", "D").lower()
