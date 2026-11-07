These are the most common Retrieval / RAG evaluation metrics:

1. Hit@K

Question: Was the correct document found in the top K results?

Formula:

1 = relevant document exists in top K
0 = otherwise

Example: Ground truth = Doc A

Top 3 results = [Doc B, Doc A, Doc C]

Hit@3 = 1

2. Precision@K (P@K)

Question: Out of the top K retrieved documents, how many are relevant?

Formula:

P@K=Relevant docs in top KKP@K = \frac{\text{Relevant docs in top K}}{K}

Example: Top 5 results → 3 relevant

P@5 = 3/5 = 0.6

3. Recall@K

Question: Out of all relevant documents, how many were retrieved in top K?

Formula:

Recall@K=Relevant docs retrieved in top KTotal relevant 

docsRecall@K = \frac{\text{Relevant docs retrieved in top K}}{\text{Total relevant docs}}

Example: Total relevant docs = 10

Retrieved relevant docs = 6

Recall@K = 6/10 = 0.6

4. MRR (Mean Reciprocal Rank)

Question: How early does the first relevant result appear?

Formula:

MRR=1RankMRR = \frac{1}{Rank}

Example: First relevant document at position 4

MRR = 1/4 = 0.25

Higher MRR = relevant results appear sooner.

5. NDCG@K (Normalized Discounted Cumulative Gain)

Question: Are highly relevant documents ranked near the top?

Considers:

Relevance score
Ranking position

Example: Relevant docs at ranks 1 and 2 → High NDCG

Relevant docs at ranks 8 and 9 → Lower NDCG

Best when relevance levels are graded (0,1,2,3).

When to Use
Metric	Measures	Best ForHit@K	Correct doc found?	Basic RAG evaluation
Precision@K	Quality of retrieved docs	Search accuracy
Recall@K	Coverage of relevant docs	Knowledge retrieval
MRR	First relevant result position	QA systems
NDCG@K	Overall ranking quality	Advanced search/retrieval
Interview One-Liner

Hit@K checks if a relevant document exists in top K results, Precision@K measures retrieval quality, Recall@K measures retrieval coverage, MRR evaluates how early the first relevant result appears, and NDCG evaluates the overall ranking quality by rewarding relevant documents appearing higher in the results.