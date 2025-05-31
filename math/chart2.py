import numpy as np
import matplotlib.pyplot as plt

def overlap_probability(n, k, u):
    """
    Estimate probability of at least one shared item among u users.
    n = catalog size
    k = average number of favorites per user
    u = number of users
    """
    # Probability that a single item is not chosen by one user: (n - k)/n
    # Probability that a single item is not chosen by any of u users: ((n - k)/n)^(k * u)
    # Probability at least one common item: 1 - ((n - k)/n)^(k * u)
    if k * u >= n:
        return 1.0  # High saturation, likely overlap
    return 1 - ((n - k) / n) ** (k * u)

catalog_sizes = np.logspace(2, 6, 100)  # Catalog size from 100 to 100,000
favorite_counts = [1, 5, 10, 20, 50]          # Average favorites per user
user_counts = [2, 3, 5, 10, 20, 50]         # Group sizes

plt.figure(figsize=(12, 7))

# For each favorite count, plot a curve for different group sizes
for k in favorite_counts:
    for u in user_counts:
        probs = [overlap_probability(n, k, u) for n in catalog_sizes]
        plt.plot(catalog_sizes, probs, label=f'{u} users, {k} favs/user')

plt.xscale('log')
plt.xlabel('Catalog Size (log scale)')
plt.ylabel('Probability of Shared Favorite in Group')
plt.title('Probability of At Least One Shared Favorite by Group Size and Catalog Size')
plt.legend()
plt.grid(True, which='both', linestyle='--', linewidth=0.5)
plt.tight_layout()
plt.show()
