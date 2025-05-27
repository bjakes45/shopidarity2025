import numpy as np
import matplotlib.pyplot as plt

# Function to calculate probability of overlap
def overlap_probability(n, k):
    # n = catalog size
    # k = average favorites per user
    # Probability no overlap: ((n-k)/n)^k
    # Probability overlap = 1 - no overlap
    return 1 - ((n - k) / n) ** k

catalog_sizes = np.logspace(2, 6, 100)  # From 100 to 1,000,000 products (log scale)
average_favorites = [5, 10, 20, 50, 100,200,500]  # Different average favorites per user

plt.figure(figsize=(10, 6))

for k in average_favorites:
    probs = [overlap_probability(n, k) if n > k else 1.0 for n in catalog_sizes]
    plt.plot(catalog_sizes, probs, label=f'Avg favorites = {k}')

plt.xscale('log')
plt.xlabel('Catalog Size (Number of Products, log scale)')
plt.ylabel('Probability of Overlap Between Two Users')
plt.title('Probability Two Users Share At Least One Favorite Product')
plt.legend()
plt.grid(True, which='both', ls='--', lw=0.5)
plt.tight_layout()
plt.show()
