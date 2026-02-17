"""Domain-level failure tracking for optimization.

When a domain fails to respond (connection timeout, etc.), we can skip
remaining links to that domain to save time and avoid hammering the server.
This module tracks failed domains and provides utilities to check if a
domain should be skipped.
"""

import threading
from typing import Set
from urllib.parse import urlparse


class FailedDomainTracker:
    """Tracks failed domains and skips future checks to them.
    
    Thread-safe tracker that maintains a set of domains that have failed.
    Once a domain reaches a failure threshold, all subsequent URLs from
    that domain are skipped without attempting to check them.
    
    Attributes:
        failure_threshold (int): Number of failures before skipping a domain.
        failed_domains (Set[str]): Set of domains marked as failed.
        domain_attempt_count (dict): Count of failed attempts per domain.
    """
    
    def __init__(self, failure_threshold: int = 3):
        """Initialize the tracker.
        
        Args:
            failure_threshold (int): Number of consecutive failures before
                skipping a domain (default: 3).
        """
        self.failure_threshold = failure_threshold
        self.failed_domains: Set[str] = set()
        self.domain_attempt_count = {}
        self._lock = threading.Lock()
    
    def is_domain_failed(self, url: str) -> bool:
        """Check if a domain is marked as failed.
        
        Args:
            url (str): The URL to check.
            
        Returns:
            bool: True if the domain is marked failed, False otherwise.
        """
        domain = urlparse(url).netloc
        with self._lock:
            return domain in self.failed_domains
    
    def mark_failure(self, url: str, error_message: str = "") -> None:
        """Mark a URL as failed, potentially marking its domain as failed.
        
        Args:
            url (str): The URL that failed.
            error_message (str): The error message (for context).
        """
        domain = urlparse(url).netloc
        with self._lock:
            if domain not in self.domain_attempt_count:
                self.domain_attempt_count[domain] = 0
            self.domain_attempt_count[domain] += 1
            
            # Mark domain as failed if threshold reached
            if self.domain_attempt_count[domain] >= self.failure_threshold:
                self.failed_domains.add(domain)
    
    def mark_success(self, url: str) -> None:
        """Mark a URL as successful, resetting domain failure count.
        
        Args:
            url (str): The URL that succeeded.
        """
        domain = urlparse(url).netloc
        with self._lock:
            # Reset failure count on success
            if domain in self.domain_attempt_count:
                del self.domain_attempt_count[domain]
    
    def get_failed_domains(self) -> Set[str]:
        """Get the set of failed domains.
        
        Returns:
            Set[str]: Copy of the failed domains set.
        """
        with self._lock:
            return set(self.failed_domains)
    
    def clear(self) -> None:
        """Clear all failure tracking data."""
        with self._lock:
            self.failed_domains.clear()
            self.domain_attempt_count.clear()
