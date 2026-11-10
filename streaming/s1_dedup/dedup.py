import re
from typing import Any, Dict, List, Optional, Tuple
from datasketch import MinHash, MinHashLSH


def tokenize_and_shingle(text: str, k: int = 2) -> List[str]:
  """Cleans text, splits into alphanumeric tokens, and generates unigrams and k-word shingles."""
  text_clean = re.sub(r"[^\w\s]", " ", text.lower())
  tokens = [w for w in text_clean.split() if len(w) > 1]
  if not tokens:
    return []
  shingles = list(tokens)
  if len(tokens) >= k:
    shingles.extend(
        [" ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)]
    )
  return shingles


def compute_minhash(tokens: List[str], num_perm: int = 128) -> MinHash:
  """Computes a MinHash signature from a list of tokens/shingles."""
  m = MinHash(num_perm=num_perm)
  for token in tokens:
    m.update(token.encode("utf-8"))
  return m


class JobDeduplicator:
  """Detects and filters near-duplicate job postings using MinHash and LSH."""

  def __init__(
      self,
      threshold: float = 0.75,
      lsh_threshold: float = 0.5,
      num_perm: int = 128,
  ):
    self.threshold = threshold
    self.num_perm = num_perm
    # LSH index operates with a lower threshold to ensure high recall of candidates
    self.lsh = MinHashLSH(threshold=lsh_threshold, num_perm=num_perm)
    self.seen_signatures: Dict[str, MinHash] = {}
    self.seen_job_ids: set = set()

  def _extract_job_text(self, job: Dict[str, Any]) -> str:
    """Combines title, company, description, and raw text for deduplication."""
    title = job.get("job_title") or job.get("title", "")
    company = job.get("company", "")
    desc = (
        job.get("description")
        or job.get("description_clean")
        or job.get("raw_text", "")
    )
    return f"{title} {company} {desc}"

  def is_duplicate(
      self, job_id: str, job_record: Dict[str, Any]
  ) -> Tuple[bool, Optional[str]]:
    """Checks if a job is an exact ID duplicate or a textual near-duplicate.

    Returns (is_dup, matched_id).
    """
    if job_id in self.seen_job_ids:
      return True, job_id

    text = self._extract_job_text(job_record)
    shingles = tokenize_and_shingle(text, k=2)
    if not shingles:
      return False, None

    m = compute_minhash(shingles, num_perm=self.num_perm)

    # 1. Recuperar candidatos del índice LSH
    candidates = self.lsh.query(m)
    for cand_id in candidates:
      cand_m = self.seen_signatures.get(cand_id)
      if cand_m and m.jaccard(cand_m) >= self.threshold:
        return True, cand_id

    # 2. Registrar como oferta original
    self.lsh.insert(job_id, m)
    self.seen_signatures[job_id] = m
    self.seen_job_ids.add(job_id)
    return False, None