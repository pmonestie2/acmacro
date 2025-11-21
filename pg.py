GRAPH = {
    'A': ['B', 'C'],
    'B': ['A', 'D', 'E'],
    'C': ['A', 'F'],
    'D': ['B'],
    'E': ['B', 'F'],
    'F': ['C', 'E']
}
def bfs():
    # BFS where we dont store the path but the parent of each node
    start = 'A'
    goal = 'F'
    queue = [start]
    visited = {start: None}  # node -> parent
    while queue:
        current = queue.pop(0)
        if current == goal:
            break
        for neighbor in GRAPH[current]:
            if neighbor not in visited:
                visited[neighbor] = current
                queue.append(neighbor)
                # reconstruct path
    path = []
    print("Visited:", visited)
    while current is not None:
        print(current)
        path.append(current)
        current = visited[current]
    path.reverse()
    print("Path from A to F:", path)


bfs()