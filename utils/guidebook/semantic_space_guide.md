### Semantic Space Annotation Guidebook 

This guidebook provides criteria for tracking and tagging state changes in the **"Semantic Space"** during the problem-solving processes of Large Reasoning Models (LRMs). 

According to cognitive science and mathematics education (Favier & Dorier, 2024), a semantic space is the solver's internal representational state. In the context of LLMs, this translates to **the structured system of mathematical constraints, relationships, and representational formats (registers) explicitly mobilized by the model within its reasoning trace.**

**IMPORTANT: Annotation Rules**
1. **Three Core Elements of Semantic Space:** A semantic space is defined by the combination of three elements:
   * **Register:** Natural language, algebra, geometry, visual, etc.
   * **Constraints:** Original problem constraints, newly added/assumed constraints, or ignored constraints.
   * **Core Tools/Frameworks:** The primary mathematical framework governing the current logic.
2. **The "Necessary but Not Sufficient" Principle:** Changes in the semantic space are driven by specific cognitive operations. Therefore, the activation of specific heuristics—**H1, H2, H5, H6, H10, and H13**—acts as a **necessary condition** for a space transition. However, their presence alone is **not a sufficient condition**. A model can use one of these heuristics merely to *reinforce* the current structuration rather than to implement a *new* one. You must evaluate the *depth* of the change.
3. **State Machine Approach:** The semantic space starts at `[ID: 0, Initial Problem State]` and evolves, receiving a new ID only when the fundamental framework changes.

---

### ⚠️ CRITICAL ANNOTATION PRINCIPLE: TRIGGER EVALUATION
**Do not automatically assign `NEW` or `RETURN` just because a necessary condition heuristic is present. Evaluate its structural impact.**

#### Decision Tree for Annotation
1. ✓ **First (Necessary Condition Check)**: This prompt is triggered because a necessary condition heuristic (**H1, H2, H5, H6, H10, or H13**) was detected in the current chunk. 
2. ✓ **Second (Sufficiency Check)**: Ask yourself: *Did this specific heuristic provoke a fundamental restructuring of the problem environment?* Or was it just a local tool used within the already established framework?
3. ✓ **Decision**: Based on the depth of the change, select `NEW`, `RETURN`, or `MAINTAIN`.

---

#### **Label Definitions and Guidelines**

**1. NEW: Creating a New Semantic Space**
* **Definition:** Tag this when the necessary condition heuristic succeeds in opening a **new working environment** by fundamentally altering the representational register, explicitly altering constraints, or introducing a completely new mathematical structural framework.
* **Examples of Triggers creating a `NEW` space:**
  * ✓ **H1 (Register):** Translating a pure natural language word problem into an algebraic equation.
  * ✓ **H2 (Reinterpretation):** Viewing a sequence of numbers conceptually as a continuous geometric function.
  * ✓ **H5 (Structural Augmentation):** Introducing a coordinate plane to a pure geometry problem, completely shifting the framework to analytic geometry.
  * ✓ **H6 (Wishful Thinking):** "Let's assume the polygon is a regular hexagon to establish a baseline." (Imposing a major structural constraint that alters the problem space).
  * ✓ **H10 (Tool):** "Let's use the Principle of Inclusion-Exclusion." (Introducing a completely new overarching mathematical framework to restructure the problem).
  * ✓ **H13b (Deriving Differently):** When presented as a full "Alternative Method", setting up a structurally distinct mathematical framework (e.g., stopping direct geometric coordinate calculations to use a completely coordinate-free determinant theorem approach).

**2. RETURN: Reverting to a Past Semantic Space**
* **Definition:** Tag this when the model explicitly abandons its current approach or finishes a sub-problem, reverting to a past semantic space stored in its memory.
* **Examples of Triggers creating a `RETURN`:**
  * ✓ **H13c (Backtracking):** "Wait, my assumption is wrong. Let's go back to the original equation." (Abandons the current flawed space to revert to a previous valid state).
  * ✓ **H13d (Sanity Check / Progress Review):** "(After finding a value in a sub-space) Now let's take this value back to the main equation." (Terminates the sub-space exploration and recalls the past semantic space).

**3. MAINTAIN: Maintaining the Current Semantic Space**
* **Definition:** Tag this when a necessary condition heuristic is used, **but its impact is local and does not fundamentally alter the Register, Constraints, or Core Tools.** It merely reinforces the current space.
* **Guidelines for Triggers resulting in `MAINTAIN`:**
  * **H10 used locally:** Recalling a standard formula (e.g., quadratic formula, Pythagorean theorem) to solve an equation already established in the current algebraic space. The tool serves the current space; it doesn't create a new one.
  * **H5 used locally:** Drawing a standard radius in a circle or defining a minor auxiliary variable that does not shift the overall mathematical framework.
  * **H1 used locally:** Writing a single natural language sentence to explain a step, but immediately continuing the algebra. The dominant register hasn't shifted.
  * **H6 used locally:** "Let's temporarily assume $x > 0$ just to drop the absolute value." (A minor, localized constraint relaxation that doesn't change the overall problem structure).
  * **H13a (Re-solving & Checking):** "Let me recalculate this to be sure." (Re-performing the exact same logical steps within the existing representational format).

---

#### 💡 Common Confusions and Clarifications (Necessary vs. Sufficient)

**"The model used a Trigger Heuristic. Does it open a `NEW` space, trigger a `RETURN`, or `MAINTAIN` the current one?"**

| Situation (Context / Heuristic) | Decision | Reason (Why?) |
| :--- | :--- | :--- |
| **Situation A:** The model is solving a geometry problem algebraically. It says, "Recall that the distance formula is $\sqrt{(x_2-x_1)^2 + (y_2-y_1)^2}$." (**H10**) | **MAINTAIN** | H10 is a trigger, but here it is just a local tool retrieved to execute calculations within the *already established* algebraic coordinate space. |
| **Situation B:** The model is struggling with a combinatorics problem. It says, "Let's use the Principle of Inclusion-Exclusion (PIE) to reframe how we count these sets." (**H10**) | **🚨 NEW** | H10 introduces a completely new, overarching mathematical framework that fundamentally restructures how the problem will be solved. |
| **Situation C:** The model says, "Let's add an auxiliary line from the center to the tangent point." (**H5**) | **MAINTAIN** | H5 is a trigger, but adding one standard line to an existing geometric figure reinforces the current structuration rather than creating a new one. |
| **Situation D:** The model says, "Let's introduce a coordinate plane with the center at (0,0) to find the lengths." (**H5** + **H1**) | **🚨 NEW** | The auxiliary construction fundamentally shifts the problem from pure geometry to analytic/coordinate geometry. |
| **Situation E:** Getting stuck and saying, "Wait, my assumption is wrong. Let's go back to the original equation." (**H13**) | **🔄 RETURN** | The model utilizes H13 (Backtracking) to abandon the current flawed space and return to a previous valid state. |
| **Situation F:** To verify the answer, the model says, "Let me recalculate the integral to be sure." (**H13**) | **MAINTAIN** | The model utilizes H13 (Re-solving/Checking) but repeats the exact same logical steps; no change to the underlying representational format or tools. |
| **Situation G:** Model solved the total combination count. It says "Alternative method: Let's confirm by finding the combinations using a different probability formula." (**H13b**) | **MAINTAIN** | The model uses an alternative method but retains the same combinatorics constraints and framework; just a local formula change. |
| **Situation H:** Model solved a geometric problem coordinates. It says "Alternative method: Let's confirm using the Gram determinant instead." (**H13b**) | **🚨 NEW** | The alternative method completely abandons coordinates (Cartesian) to shift to a coordinate-free linear algebra framework. |

---

#### Final Reminders
1. **The "Tool vs. Stage" Analogy:** Heuristics are the 'tools' used to build or explore. The semantic space is the 'stage'. Pulling out a hammer (activating H1, H2, H5, H6, H10, or H13) is *necessary* to build a new stage, but sometimes you just use the hammer to fix a loose nail on the *current* stage (`MAINTAIN`).
2. **Context is Everything:** Always evaluate the triggered heuristic relative to the previous chunk. If the Register, Constraints, and Core Framework remain functionally identical after the heuristic is applied, the semantic space has not changed.