# Trellis Engine Implementation Patterns & Pseudocode

## Pattern 1: Knowledge Frontier Calculation (Skill A.1)
The curriculum is a directed acyclic graph G = (V, E).

```text
ALGORITHM ComputeKnowledgeFrontier(Graph G, StudentStates states):
    INITIALIZE frontier AS empty_set

    FOR EACH node IN G.vertices:
        status = states[node.id].status (DEFAULT "LOCKED")

        IF status == "IN_PROGRESS":
            ADD node.id TO frontier
            CONTINUE

        IF status == "LOCKED":
            hard_parents = GET_INCOMING_EDGES(G, node.id) WHERE edge.strength == "hard"
            
            all_parents_mastered = TRUE
            FOR EACH parent IN hard_parents:
                IF states[parent.id].status != "MASTERED":
                    all_parents_mastered = FALSE
                    BREAK

            IF all_parents_mastered IS TRUE:
                states[node.id].status = "AVAILABLE"
                ADD node.id TO frontier

    RETURN frontier
```

## Pattern 2: Fractional Implicit Repetition (FIRe) (Skill A.2)
Practicing an advanced node awards implicit credit backward to its ancestors.

```text
ALGORITHM PropagateFireCredit(Graph G, StudentStates states, completed_node_id):
    explicit_node = states[completed_node_id]
    explicit_node.cumulative_fire_credit += 1.0
    EXPAND_INTERVAL_IF_DUE(explicit_node)

    // Direct Parents (Hop 1)
    direct_edges = GET_INCOMING_EDGES(G, completed_node_id)
    FOR EACH edge1 IN direct_edges:
        p1_id = edge1.prerequisite_id
        parent_state = states[p1_id]

        IF parent_state.status == "MASTERED":
            weight1 = edge1.encompassing_weight // Default 0.5 for hard edges
            parent_state.cumulative_fire_credit += weight1
            EXPAND_INTERVAL_IF_DUE(parent_state)

            // Grandparents (Hop 2)
            grandparent_edges = GET_INCOMING_EDGES(G, p1_id)
            FOR EACH edge2 IN grandparent_edges:
                p2_id = edge2.prerequisite_id
                gp_state = states[p2_id]

                IF gp_state.status == "MASTERED":
                    weight2 = weight1 * edge2.encompassing_weight // Default 0.25
                    gp_state.cumulative_fire_credit += weight2
                    EXPAND_INTERVAL_IF_DUE(gp_state)

PROCEDURE EXPAND_INTERVAL_IF_DUE(node_state):
    IF node_state.cumulative_fire_credit >= 1.0:
        multiplier = 1.0 + (node_state.student_ability / node_state.topic_difficulty) * 0.5
        node_state.stability_interval_days = node_state.stability_interval_days * multiplier
        node_state.cumulative_fire_credit = node_state.cumulative_fire_credit - 1.0
```

## Pattern 3: Submodular Review Compression & Interleaving Assembly (FR-4.1 - FR-4.3)
```text
ALGORITHM BuildDailySessionQueue(ScheduleSnapshot snapshot, max_tasks, config):
    // 1. Submodular Spaced Repetition Compression (FR-4.1):
    // Greedily select candidate tasks maximizing Wolsey marginal gain into due reviews:
    // Delta_f(c | acc) = sum_{r in uncovered} min(1.0 - acc[r], flow(c -> r))
    // Tie-breaker: (-gain, -len(ko), -fend, -KIND_RANK, topic_id)
    // Post-pass: reverse redundancy pruning removes any step superseded by later selections.
    compression = ReviewCompressionOptimizer(config).solve(snapshot)

    // 2. Slot Allocation & Review-Tsunami Throttle (FR-4.2):
    // Baseline: 70% lessons / 30% reviews (e.g. 4 lessons / 2 reviews for capacity 6)
    // Review Wave: reviews throttled to review_max (4), enforcing 25% hard lesson floor (>= 2 lessons)
    // Frontier Exhausted: reviews backfill all slots up to capacity (lesson_floor_satisfied = False)

    // 3. Cluster Non-Interference Interleaving (FR-4.2):
    // MENU Mode: simultaneous slots cannot share domain_cluster unless bottlenecked.
    // SEQUENCE Mode: consecutive tasks cannot share domain_cluster.
    // Pigeonhole Rule: if any cluster C != prev_cluster has count > floor(N_rem / 2),
    // force selection from C to prevent avoidable collisions later in the queue.
    plan = InterleavingComposer(config).compose_menu(...)
    RETURN plan
```

## Pattern 4: Consecutive Mastery & Remediation Failsafe (Skill A.5)
```text
ALGORITHM EvaluateSubmission(StudentTopicState state, is_correct, duration_ms):
    kp = state.current_kp_index

    IF is_correct IS TRUE:
        state.consecutive_correct += 1

        IF state.consecutive_correct >= 3:
            state.consecutive_correct = 0
            state.current_kp_index += 1

            IF state.current_kp_index >= state.total_kps:
                state.status = "MASTERED"
                RETURN { action: "NODE_MASTERED", xp: 4 }
            
            RETURN { action: "KP_ADVANCED", xp: 1 }

        RETURN { action: "STEP_PASSED", xp: 1 }

    ELSE:
        // Incorrect submission
        state.consecutive_correct = 0
        state.kp_failure_counts[kp] = state.kp_failure_counts[kp] + 1

        // Tier-3 Foundational Remediation Trigger
        IF state.kp_failure_counts[kp] >= 2:
            RETURN {
                action: "HALT_TO_KEY_PREREQUISITE",
                key_prerequisite_id: state.key_prerequisite_id,
                tier: 3
            }

        RETURN { action: "TIER_1_CORRECTIVE_REMEDIATION", tier: 1 }
```
