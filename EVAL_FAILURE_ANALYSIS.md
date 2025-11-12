# Evaluation Failure Analysis

## Summary
- **Total Evaluations**: 47
- **Correct**: 37 (78.7%)
- **Failures**: 10 (21.3%)
  - All failures are marked as "incorrect" (no partial failures)

## Failure Patterns

### 1. Calculation Errors (6 failures)
These failures involve incorrect mathematical calculations or wrong DRM values:

#### A5.1 - Overstacking DRM
- **Issue**: Model says +1 DRM, correct answer is -1 DRM
- **Root Cause**: Confuses penalty direction - attacking FROM overstacked hex vs being attacked WHILE overstacked
- **Fix**: System instructions need to emphasize reading rules carefully for direction of modifiers

#### A6.4 - Blind Hexes Calculation
- **Issue**: Model calculates 2-level elevation advantage, should be 1 level
- **Root Cause**: Incorrectly calculates: Level 3 - 1 level obstacle = 2 levels (but should be 1 level advantage over obstacle)
- **Fix**: Need better instructions for elevation calculations

#### A8.26 - Residual Fire Power
- **Issue**: Model calculates 2 FP, correct answer is 4 FP
- **Root Cause**: Incorrectly applies IFT column shifts for hindrances
- **Fix**: Need clearer instructions on RFP calculation with hindrances

#### A14.4 - Sniper Check DRM
- **Issue**: Model says "2 or less", correct answer is "4 or less" (accounting for -2 DRM)
- **Root Cause**: Doesn't apply the -2 DRM modifier for squad
- **Fix**: System instructions should emphasize applying all relevant DRMs

#### A24.2 - DRM Calculation
- **Issue**: Model uses SMOKE +1 (should be +2) and doesn't apply Wall Advantage +2
- **Root Cause**: Wrong DRM values and missing modifier
- **Fix**: Need to ensure model retrieves complete rule text

#### A9.222 - Fire Lane DRM
- **Issue**: Model applies positive Smoke DRM, correct answer is DRM = 0
- **Root Cause**: Doesn't understand that Smoke doesn't provide DRM for Fire Lane (only cancels FFMO)
- **Fix**: Need better instructions on conditional rule applications

### 2. Rule Direction/Perspective Errors (2 failures)

#### A11.5 - Close Combat vs AFV
- **Issue**: Model focuses on AFV attacking infantry, but question asks about infantry attacking AFV
- **Root Cause**: Answers from wrong perspective - doesn't identify who is attacking whom
- **Fix**: System instructions should emphasize identifying the attacker/defender perspective

#### A11.21 - Close Combat DR Results
- **Issue**: Model says elimination/casualty reduction, correct answer is withdrawal
- **Root Cause**: Confuses different DR result outcomes
- **Fix**: Need clearer instructions on reading specific DR result tables

### 3. Rule Interpretation Errors (2 failures)

#### A12.15 - Concealment Loss
- **Issue**: Model says "all concealment is lost", correct answer is "at least one unit must be revealed"
- **Root Cause**: Over-interprets the rule - doesn't read "at least one" carefully
- **Fix**: System instructions should emphasize reading exact wording, not inferring

#### A26.11 - Control with DM Units
- **Issue**: Model says DM units don't prevent control, correct answer is they do
- **Root Cause**: Incorrectly interprets that broken units don't prevent control (but DM units are still armed)
- **Fix**: Need better understanding of unit states (DM vs broken)

## Root Causes

### 1. Vector Store Issues
- **Current**: Using v4 (simple fixed-size chunking, no section metadata)
- **Problem**: Chunks may not contain complete rule context
- **Impact**: Model may not retrieve all relevant rule text for complex calculations

### 2. System Instructions
Current instructions are good but may need enhancement for:
- **Calculation precision**: Emphasize showing all steps and checking math
- **Rule direction**: Identify attacker/defender perspective clearly
- **Exact wording**: Read rules literally, don't infer or generalize
- **DRM application**: List all applicable modifiers and apply them correctly

### 3. Model Limitations
- Some failures suggest the model is making logical inferences rather than reading exact rule text
- May need to emphasize "quote the rule exactly" rather than "explain the rule"

## Recommended Fixes

### Priority 1: Enhance System Instructions
Add to `app/config.py` ASL_SYSTEM_INSTRUCTIONS:

```python
- For calculations, show ALL steps explicitly and verify the final result
- When applying DRMs, list each modifier separately with its value, then sum them
- Identify the perspective: who is attacking whom, who is moving, etc.
- Read rules literally - use exact wording from the rulebook, don't infer or generalize
- If a rule says "at least one" or "may", don't interpret it as "all" or "must"
- For DR results, refer to the specific table/rule for that DR value
```

### Priority 2: Consider Vector Store Strategy
- **Option A**: Switch back to v2 (section metadata chunking) - may provide better context
- **Option B**: Keep v4 but increase chunk size/overlap to capture more context
- **Option C**: Hybrid approach - use section-based chunking for rule sections, simple chunking for examples

### Priority 3: Add Validation Prompts
For calculation questions, add a follow-up validation:
- "Verify your calculation step-by-step"
- "List all DRMs that apply to this situation"

### Priority 4: Improve RAG Retrieval
- Ensure questions retrieve the specific rule section
- May need to enhance question-to-section mapping in the vector store

## Testing Recommendations

1. Re-run evaluations after updating system instructions
2. Manually verify a few failure cases to see if they're now correct
3. Add unit tests for specific calculation scenarios
4. Consider adding a "calculation verification" step in the evaluation process

