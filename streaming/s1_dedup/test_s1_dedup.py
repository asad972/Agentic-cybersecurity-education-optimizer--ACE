from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from streaming.s1_dedup.dedup import JobDeduplicator


def test_deduplicator():
  dedup = JobDeduplicator(threshold=0.75, lsh_threshold=0.5)

  job_original = {
      "job_id": "job-001",
      "job_title": "Senior SOC Analyst",
      "company": "Nordic Shield",
      "country": "SE",
      "description": (
          "Looking for a Senior SOC Analyst with extensive experience in"
          " Splunk, Wireshark, incident response and triage."
      ),
  }

  job_duplicate = {
      "job_id": "job-002",
      "job_title": "Senior SOC Analyst",
      "company": "Nordic Shield",
      "country": "SE",
      "description": (
          "We are looking for a Senior SOC Analyst with extensive experience in"
          " Splunk, Wireshark, incident response, and triage."
      ),
  }

  job_different = {
      "job_id": "job-003",
      "job_title": "Penetration Tester",
      "company": "CyberOffense Ltd",
      "country": "NL",
      "description": (
          "Junior offensive penetration tester required to conduct web"
          " application audits using Burp Suite and Metasploit."
      ),
  }

  # 1. El primer trabajo debe ser indexado como original
  is_dup, match = dedup.is_duplicate("job-001", job_original)
  assert not is_dup, "job-001 should be marked as original"
  print("✓ job-001 indexado como original")

  # 2. El segundo trabajo (variación leve) debe detectarse como duplicado de job-001
  is_dup, match = dedup.is_duplicate("job-002", job_duplicate)
  assert is_dup and match == "job-001", (
      f"job-002 should match job-001, got is_dup={is_dup}, match={match}"
  )
  print(f"✓ job-002 detectado correctamente como duplicado de {match}")

  # 3. El tercer trabajo debe ser indexado como original
  is_dup, match = dedup.is_duplicate("job-003", job_different)
  assert not is_dup, "job-003 should be marked as original"
  print("✓ job-003 indexado como original")

  print("\nTodos los tests de deduplicación MinHash LSH han pasado con éxito.")


if __name__ == "__main__":
  test_deduplicator()