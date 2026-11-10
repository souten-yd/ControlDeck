# Add-on local retrieval AI (work in progress)

PatentBuddy GraphRAG needs Host-owned embeddings and reranking. Add a bounded, local-only retrieval API under the existing ai.inference permission. Host selects registered llama.cpp embedding/reranker instances, validates loopback and the local GGUF, obtains the existing gateway Broker lease, and releases it on every exit. No add-on-selected URL/model or cloud fallback.

Return only vectors/ranks plus an opaque model configuration revision (file metadata fingerprint, not a content hash), never model paths, aliases, credentials, prompts, or provider URLs. Missing models and malformed responses fail explicitly. Test request bounds, ownership/disable gates, finite vectors/ranks, network redirect/proxy rejection, cancellation and lease cleanup. Verify live through PatentBuddy before claiming integration complete.
