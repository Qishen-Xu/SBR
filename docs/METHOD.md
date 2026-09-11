# Current method and training protocol

Version 1.3.0 source-only distribution: native unary node predicates, canonical structural candidate order, and no PCA computation, filtering, or ranking. Models and datasets must be supplied separately.

## 1. Input representation

The extractor reads `SKILL.md` and deterministic references to runnable filenames under `scripts/`. It uses five frozen prompt fragments, an explicitly configured LLM, schema validation, and script placeholder injection. Long documents exceeding 16,000 characters are split at markdown headings with a target of 12,000 characters and a 1,500-character minimum for an accumulated chunk; each chunk retains frontmatter. These are soft boundaries: one unusually long section is not truncated. Nodes merge by canonical ID, constraints by `(text, source_section)`, and edges by `(source, target, edge_type, source_section)`.

The base relations are `READ, WRITE, EXEC, FETCH, SEND, SEARCH, ASK, SPAWN`. Entity types are `FILE, NETWORK, CREDENTIAL, PACKAGE, USER`; recognized implicit tool names provide `AGENT` endpoints. Graph extraction does not analyze script bodies or fetch linked content. A new graph records dropped rows and placeholder decisions.

Extraction metadata identifies the configured LLM, the frozen system prompt hash, and the hash of UTF-8 input text after universal-newline normalization. Example provenance separately hashes the original file bytes. Hosted API extraction requires no local GPU.

## 2. Local text model

Each nonempty node constraint and edge description becomes one local record. The record retains its node identity or edge index. Names, labels, and family identifiers are not concatenated to the text feature input.

The exact preprocessing order is URL, email, IP, path, and long-identifier replacement; whitespace normalization and lowercasing; then replacement of an explicitly listed set of security-related words with `SECURITY_TERM`. This is regex substitution in `text_model.py`, not a language model. Because the order is fixed, an all-caps long token can be replaced as an identifier before the security-word pass.

TF-IDF uses word unigrams and bigrams, `min_df=4`, `max_features=30000`, `sublinear_tf=True`, `strip_accents='unicode'`, smoothed IDF, and L2 row normalization. Document frequency counts local records, not skills. No embeddings, NLI models, clustering, or manually chosen graph risk predicates are used.

For local record vector \(x_{ij}\) from skill \(i\), let \(z_{ij}=w^T x_{ij}+b\). The pooled training logit is

\[
g_i=\log\left(\frac{1}{n_i}\sum_{j=1}^{n_i}\exp(z_{ij})\right).
\]

The objective is class-balanced binary logistic loss on skill labels plus \(\lambda\|w\|_2^2/2\). The bias is unpenalized. An empty bag is represented by one empty text for optimization, but emits no local facts. L-BFGS-B starts at zero, uses `maxiter=500` and `ftol=1e-9`, and a nonconverged fit is treated as a training error.

The selected current \(\lambda\) is `1e-5`. The optional training-side search considers `1e-3, 1e-4, 1e-5`. The local score is \(p_{ij}=\sigma(z_{ij})\). It is learned through skill-level supervision; it is not a separately annotated semantic truth value or a calibrated permission judgment.

## 3. ML predicates and unified mining

For every satisfied threshold \(t\in\{0.3,0.5,0.7,0.9\}\), a record emits a predicate:

- Node text attached to entity \(u\): `ML_NODE_LOCAL_GE_T(u)`.
- Edge description attached to edge \((u,v)\): `ML_EDGE_LOCAL_GE_T(u,v)`.

Predicates are nested: a score of 0.71 emits the 30, 50, and 70 predicates. Binding scope comes from the original graph record, not from a scope feature in TF-IDF. Multiple qualifying records can support the same logical fact; logical facts are deduplicated during mining and matching.

These facts are inserted **before rule discovery**. The miner can mix base and ML predicates in the same rule body. Every head remains a base behavior predicate. Bodies contain at most two atoms; the rule must be connected and every head variable must occur in the body.

Mining uses per-skill deduplicated support, minimum support 2, standard confidence 0.3, and head coverage 0.01. Candidate endpoint type signatures come from observed training facts. The AMIE-style search enumerates connected refinements and preserves concrete variable joins; this repository does not claim to be the AMIE software implementation.

Retained candidate rules enter the selector in canonical structural order. Structural, ML-only, and mixed bodies all enter the same matching and weighting path. Every head is a base behavior relation.

## 4. What rule application means

For a mined rule \(B_1\land\cdots\land B_k\Rightarrow H\), its feature is

\[
h_r(G)=\mathbf{1}[\exists\theta:\ G\models B_1\theta\land\cdots\land B_k\theta\land H\theta].
\]

Both the body and head must be observed under one consistent, type-correct assignment. This is a graph-pattern representation, not forward chaining and not a search for violated implications. Two different FILE entities cannot satisfy a shared variable merely because they have the same type.

The final detector is

\[
s(G)=\sigma\left(\beta_0+\sum_r\beta_rh_r(G)\right),\qquad
\hat y=\mathbf{1}[s(G)\ge\tau].
\]

Rule coefficients and the intercept are learned from training skill labels using class-balanced logistic regression with L1 regularization and the `liblinear` solver. The current solver uses its standard synthetic-intercept convention; unlike the local model, its intercept follows liblinear's regularization behavior. Positive and negative weights both contribute. There is no manually assigned malicious-rule label and no separate raw-text feature entering this final classifier.

The selector fits all retained candidate columns in canonical signature order. Lossless post-fit compilation sums coefficients of logically equivalent joint-match queries without refitting the selector or threshold. Candidate counts, nonzero coefficients, fitted parameters and deployment size depend on the supplied training data and numerical environment.

Compilation uses nested threshold implications and type-preserving substitutions to remove redundant atoms. The search is bounded to three joint atoms. Variable renaming and head/body roles do not change a Boolean joint-match feature. Original expressions and merged-feature provenance remain in the model. The training command checks that weighted scores are preserved within numerical tolerance. This equivalence assumes facts produced by the documented materializer.

The training selector still sees the full candidate matrix. Original rules, fitted parameters, and the matrix are saved under `training/`; `training/compression.json` records representative column indices and verifies that weighted training scores agree before and after compilation. Only the compiled nonzero features are exported in `model/`. No empirical hit-vector deduplication, retraining after compression, or additional parameter search is part of this export step.

Training hit-vector deduplication is a separate empirical reduction: different queries may coincide on training samples and disagree on future graphs. It must be selected and validated as a model change, rather than treated as lossless compilation.

## 5. Splits, selection, and evaluation boundaries

The caller supplies the outer training/test split and family identifiers in the manifest. Families must not cross that boundary. Family grouping should reflect shared source or related variants; a name-prefix heuristic alone does not establish the absence of semantic duplication.

Within outer training, three-fold `StratifiedGroupKFold` with seed `20260907` generates out-of-fold local scores for mining. Each training sample is scored by a local model that did not fit that sample or family. After selecting the local configuration, one final local model fits all training samples for future inputs.

The rule selector uses three-fold `StratifiedGroupKFold` with seed `20260906`. Candidate `C` values are `0.01, 0.03, 0.1, 0.3, 1.0`. Thresholds maximize training OOF F1 under FPR ≤ 0.37, breaking ties by precision, lower FPR, then higher threshold. When more than 800 distinct scores exist, 801 quantiles are used alongside `0, 0.5, 1, 1.000001`. Selector candidates tie-break by F1, precision, and lower FPR. The reported threshold is rounded to eight decimals as in the frozen reference.

**Selector CV reuses the training predicate and mined-rule representation; it does not refit the complete upstream pipeline inside each selector fold.** Accordingly, its inner score is a parameter-selection diagnostic, not an independent generalization estimate. The final model and all candidates are constructed using outer-training data; evaluation applies them to the fixed outer test samples. No five-model ensemble is deployed.

Execution tests on artificial inputs do not establish detection accuracy. Evaluate on appropriately held-out data, and distinguish cached-graph evaluation from a new graph extraction run. Dataset evaluation reports any explicitly supplied graph coverage exceptions.

Candidate rules use canonical structural order, without a quality ranking or top-K truncation. PCA (partial completeness assumption confidence) is not computed, filtered, or used for ordering. Changing column order can change the numerical L1 solution even when candidate semantics are unchanged; record the column order and numerical environment when comparing fitted models.
