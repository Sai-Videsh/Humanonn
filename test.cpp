#include <iostream>
#include <vector>
#include <algorithm>
#include <cmath>

using namespace std;

int main() {
    // Optimize standard I/O operations for competitive programming
    ios_base::sync_with_stdio(false);
    cin.tie(NULL);

    int n;
    if (!(cin >> n)) return 0;

    vector<int> A(n + 1);
    vector<vector<int>> rev_adj(n + 1);
    for (int i = 1; i <= n; ++i) {
        cin >> A[i];
        rev_adj[A[i]].push_back(i);
    }

    vector<int> visited(n + 1, 0);
    vector<int> component_id(n + 1, 0);
    vector<long long> cycle_sizes;
    vector<long long> component_sizes;
    
    int num_components = 0;

    // 1. Identify cycles and find all nodes belonging to each cycle
    for (int i = 1; i <= n; ++i) {
        if (visited[i] == 0) {
            int curr = i;
            vector<int> path;
            while (curr <= n && visited[curr] == 0) {
                visited[curr] = 1;
                path.push_back(curr);
                curr = A[curr];
            }

            if (curr <= n && visited[curr] == 1) {
                // Cycle found!
                num_components++;
                auto it = find(path.begin(), path.end(), curr);
                long long c_size = distance(it, path.end());
                cycle_sizes.push_back(c_size);

                // Mark cycle nodes with their component ID
                for (; it != path.end(); ++it) {
                    component_id[*it] = num_components;
                }
            }

            for (int node : path) {
                visited[node] = 2; // Mark as fully processed
            }
        }
    }

    // 2. Multi-source BFS/DFS backwards from cycle nodes to compute total component size
    component_sizes.resize(num_components + 1, 0);
    vector<int> q;
    vector<long long> depth(n + 1, 0);
    
    for (int i = 1; i <= n; ++i) {
        if (component_id[i] > 0) {
            q.push_back(i);
            depth[i] = 0; // cycle nodes have base distance 0 to the cycle
        }
    }

    // Process trees pointing to cycles
    size_t head = 0;
    while (head < q.size()) {
        int u = q[head++];
        component_sizes[component_id[u]]++;
        for (int v : rev_adj[u]) {
            if (component_id[v] == 0) {
                component_id[v] = component_id[u];
                depth[v] = depth[u] + 1;
                q.push_back(v);
            }
        }
    }

    // Calculate base reach values for every node before any component merges
    long long total_base_reach = 0;
    for (int i = 1; i <= n; ++i) {
        int comp = component_id[i];
        total_base_reach += (depth[i] + cycle_sizes[comp - 1]);
    }

    // 3. Collect component information for merging optimization
    // Pair: {component_size, cycle_size}
    vector<pair<long long, long long>> comps;
    for (int i = 1; i <= num_components; ++i) {
        comps.push_back({component_sizes[i], cycle_sizes[i - 1]});
    }

    // Sort by component size descending
    sort(comps.rbegin(), comps.rend());

    // We can merge up to 3 components using 2 edge modifications
    long long ans = total_base_reach;

    if (num_components >= 3) {
        long long S1 = comps[0].first, C1 = comps[0].second;
        long long S2 = comps[1].first, C2 = comps[1].second;
        long long S3 = comps[2].first, C3 = comps[2].second;

        // Gain formula from linking 3 components into a single cycle network:
        long long gain = S1 * (C2 + C3) + S2 * (C1 + C3) + S3 * (C1 + C2);
        ans += gain;
    } 
    else if (num_components == 2) {
        long long S1 = comps[0].first, C1 = comps[0].second;
        long long S2 = comps[1].first, C2 = comps[1].second;

        long long gain = S1 * C2 + S2 * C1;
        ans += gain;
    }

    cout << ans << "\n";

    return 0;
}