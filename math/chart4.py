import numpy as np
import matplotlib.pyplot as plt
from collections import defaultdict

# Parameters
num_users = 2000
favorites_per_user = 50
catalog_size = 10000
exponents = [0.5, 0.8, 1.0, 1.2]

# Create a subplot for each exponent
fig, axs = plt.subplots(2, 2, figsize=(14, 10))
axs = axs.flatten()

for i, exponent in enumerate(exponents):
    product_favorited_by = defaultdict(set)

    # Generate Zipf probabilities
    weights = 1 / np.arange(1, catalog_size + 1) ** exponent
    probabilities = weights / weights.sum()

    # Simulate favoriting
    for user_id in range(num_users):
        favorites = np.random.choice(catalog_size, size=favorites_per_user, replace=False, p=probabilities)
        for p in favorites:
            product_favorited_by[p].add(user_id)

    # Count group sizes
    group_sizes = [len(users) for users in product_favorited_by.values() if len(users) > 1]

    # Plot
    axs[i].hist(group_sizes, bins=range(2, max(group_sizes)+2), align='left', edgecolor='black')
    axs[i].set_title(f'Zipf Exponent = {exponent}')
    axs[i].set_xlabel('Group Size')
    axs[i].set_ylabel('Number of Products')
    axs[i].grid(True)

plt.suptitle(f'Group Sizes From Zipf-Weighted Favorites\n(users={num_users}, favorites={favorites_per_user}, catalog={catalog_size})', fontsize=14)
plt.tight_layout(rect=[0, 0, 1, 0.95])
plt.show()
