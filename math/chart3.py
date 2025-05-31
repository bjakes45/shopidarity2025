import numpy as np
import matplotlib.pyplot as plt

def expected_matches(n, k, u):
    """
    Estimate expected number of matching pairs of users (shared favorite).
    n = catalog size
    k = average favorites per user
    u = number of users
    """
    p = k / n
    expected_per_product = (u * p) * (u * p - 1) / 2
    total_matches = n * expected_per_product
    return max(0, total_matches)

catalog_sizes = np.logspace(2, 6, 100)  # Catalog size from 100 to 1,000,000
favorite_counts = [10, 20, 50]          # Favorites per user
user_counts = [10, 50, 100, 200]        # Number of users

plt.figure(figsize=(12, 7))

for k in favorite_counts:
    for u in user_counts:
        matches = [expected_matches(n, k, u) for n in catalog_sizes]
        plt.plot(catalog_sizes, matches, label=f'{u} users, {k} favs/user')

plt.xscale('log')
plt.yscale('log')
plt.xlabel('Catalog Size (log scale)')
plt.ylabel('Expected Matches (log scale)')
plt.title('Expected Number of Shared Favorite Matches')
plt.legend()
plt.grid(True, which='both', linestyle='--', linewidth=0.5)
plt.tight_layout()
plt.show()
