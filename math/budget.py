import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

def forecast_budget(
    months=120,
    initial_active_users=20,
    monthly_user_growth=100,
    max_avg_lookups_per_day=5,
    total_unique_upcs=663_302_369,
    upc_relevance= .66,
    product_size_kb=2.5,
    storage_cost_per_gb=1.0,
    upc_api_plan='free',
    use_logistic_growth=True,
    carrying_capacity=1_000_000,
    growth_rate=0.14
):
    api_plans = {
        'free': {'monthly_limit': 3_000, 'monthly_fee': 0, 'overage_rate_per_call': 0},
        'dev':  {'monthly_limit': 600_000, 'monthly_fee': 99, 'overage_rate_per_call': 0.0004},
        'pro':  {'monthly_limit': 4_500_000, 'monthly_fee': 699, 'overage_rate_per_call': 0.0004},
    }

    plan_order = ['free', 'dev', 'pro']

    data = {
        'Month': [], 'Active Users': [], 'Avg Lookups/Day': [], 'Monthly UPC Lookups': [],
        'Cumulative UPCs Looked Up': [], 'UPC Saturation (%)': [], 'UPC API Calls Billed': [],
        'UPC API Cost': [], 'API Plan': [], 'UPC Storage Size (GB)': [], 'User Storage Size (GB)': [],
        'UPC Storage Cost': [], 'User Storage Cost': [], 'Storage Cost': [], 'Total Cost': [],
        'Break Even Revenue/User': []
    }
 
    total_unique_upcs = total_unique_upcs * upc_relevance
    cumulative_upcs_looked_up = 0

    for month in range(1, months + 1):
        selected_plan_key = upc_api_plan
        
        # User growth
        if use_logistic_growth:
            exponent = -growth_rate * (month - 1)
            active_users = carrying_capacity / (
                1 + ((carrying_capacity - initial_active_users) / initial_active_users) * np.exp(exponent)
            )
        else:
            active_users = initial_active_users + (month - 1) * monthly_user_growth
        
        active_users = int(active_users)  # cast to int for data size logic

        # UPC depletion logic
        remaining_upcs = max(total_unique_upcs - cumulative_upcs_looked_up, 1)
        percent_found = 1/10
        decay_base = (1.0-percent_found)**-1  # every time percentage of UPCs are discovered, reduce lookups by 1
        reduction_steps = int(np.log(total_unique_upcs / remaining_upcs) / np.log(decay_base))
        if max_avg_lookups_per_day - reduction_steps >= 1:
            effective_avg_lookups = max_avg_lookups_per_day - reduction_steps
        else:
            underflow_steps = reduction_steps - int(max_avg_lookups_per_day - 1)
            effective_avg_lookups = 1 / (2 ** underflow_steps)

        # Monthly lookups and saturation
        max_monthly_lookups = active_users * effective_avg_lookups * 30
        monthly_upc_lookups = int(min(max_monthly_lookups, remaining_upcs))
        cumulative_upcs_looked_up += monthly_upc_lookups
        saturation_pct = (cumulative_upcs_looked_up / total_unique_upcs) * 100

        # API Plan logic
        api_calls = monthly_upc_lookups
        current_plan_index = plan_order.index(selected_plan_key)

        while current_plan_index < len(plan_order):
            plan_key = plan_order[current_plan_index]
            plan = api_plans[plan_key]

            if api_calls <= plan['monthly_limit']:
                selected_plan_key = plan_key
                api_cost = plan['monthly_fee']
                break
            elif current_plan_index == len(plan_order) - 1:
                selected_plan_key = plan_key
                overage_calls = api_calls - plan['monthly_limit']
                api_cost = plan['monthly_fee'] + overage_calls * plan['overage_rate_per_call']
                break
            else:
                current_plan_index += 1

        # Storage sizes (in KB)
        upc_data_kb = cumulative_upcs_looked_up * product_size_kb
        user_data_kb = active_users * product_size_kb * 500

        # Convert to GB
        upc_storage_gb = upc_data_kb / (1024 * 1024)
        user_storage_gb = user_data_kb / (1024 * 1024)

        # Costs
        upc_storage_cost = upc_storage_gb * storage_cost_per_gb
        user_storage_cost = user_storage_gb * storage_cost_per_gb
        storage_cost = upc_storage_cost + user_storage_cost

        total_cost = api_cost + storage_cost
        
        # Calculate break even revenue per user (avoid division by zero)
        if active_users > 0:
            break_even_revenue_per_user = total_cost / active_users
        else:
            break_even_revenue_per_user = 0

        # Store results
        data['Month'].append(month)
        data['Active Users'].append(active_users)
        data['Avg Lookups/Day'].append(effective_avg_lookups)
        data['Monthly UPC Lookups'].append(monthly_upc_lookups)
        data['Cumulative UPCs Looked Up'].append(cumulative_upcs_looked_up)
        data['UPC Saturation (%)'].append(saturation_pct)
        data['UPC API Calls Billed'].append(api_calls)
        data['UPC API Cost'].append(api_cost)
        data['API Plan'].append(selected_plan_key)
        data['UPC Storage Size (GB)'].append(upc_storage_gb)
        data['User Storage Size (GB)'].append(user_storage_gb)
        data['UPC Storage Cost'].append(upc_storage_cost)
        data['User Storage Cost'].append(user_storage_cost)
        data['Storage Cost'].append(storage_cost)
        data['Total Cost'].append(total_cost)
        data['Break Even Revenue/User'].append(break_even_revenue_per_user)

    df = pd.DataFrame(data)

    # Create side-by-side subplots
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))

    # Plot 1: Cost breakdown
    ax1.plot(df['Month'], df['UPC API Cost'], label='UPC API Cost ($)')
    ax1.plot(df['Month'], df['UPC Storage Cost'], label='UPC Storage Cost ($)')
    ax1.plot(df['Month'], df['User Storage Cost'], label='User Data Storage Cost ($)')
    ax1.plot(df['Month'], df['Storage Cost'], label='Total Storage Cost ($)', linestyle='--')
    ax1.plot(df['Month'], df['Total Cost'], label='Total Cost ($)', linewidth=2, color='black')
    ax1.set_xlabel('Month')
    ax1.set_ylabel('Cost ($)')
    ax1.set_title('Shopidarity Budget Forecast\n(UPC API + Storage Breakdown)')
    ax1.legend()
    ax1.grid(True)

    # Plot 2: Break even revenue per user
    ax2.plot(df['Month'], df['Break Even Revenue/User'], label='Break Even Revenue per User ($)', color='purple', linewidth=2)
    ax2.set_xlabel('Month')
    ax2.set_ylabel('Revenue per User ($)')
    ax2.set_title('Break Even Revenue per User Over Time')
    ax2.grid(True)
    ax2.legend()

    plt.tight_layout()
    plt.show()

        # Save to CSV
    df.to_csv('shopidarity_budget_forecast.csv', index=False)

    return df


    return df

# Run and preview
df_forecast = forecast_budget()
print(df_forecast[['Month', 'Active Users', 'Total Cost', 'Break Even Revenue/User']].head(15))
