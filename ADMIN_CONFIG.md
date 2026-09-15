# RGNODES™ Admin Configuration

All runtime controls below persist in SQLite and survive bot restarts. Only the configured `ADMIN_ID` can change them.

## Runtime settings

Use:

```text
/admin-config action:show
/admin-config action:set key:deploy_cost value:500
/admin-config action:set key:slot_price value:1000
/admin-config action:set key:max_user_slots value:25
/admin-config action:set key:default_os value:ubuntu-24.04
/admin-config action:set key:default_ram value:4g
/admin-config action:set key:default_cpu value:1
/admin-config action:set key:default_disk value:10g
/admin-config action:set key:default_location value:SG
/admin-config action:set key:total_running_limit value:1000
/admin-config action:set key:global_create_limit value:10000
/admin-config action:set key:max_ports_per_vps value:10
/admin-config action:set key:port_range_start value:20000
/admin-config action:set key:port_range_end value:40000
/admin-config action:set key:min_ram value:256m
/admin-config action:set key:max_ram value:256g
/admin-config action:set key:min_disk value:1g
/admin-config action:set key:max_disk value:10t
/admin-config action:set key:min_cpu value:0.1
/admin-config action:set key:max_cpu value:64
```

Prefix equivalents use `-admin-config ...`. Reset a value with `action:reset`.

## Economy

Deploy cost, slot price, reward ranges, invitation conversion rate, cooldowns, and user slot caps are runtime configurable. Coin deductions are atomic; failed VPS deployment refunds its deployment charge.

## Plans

Administrators can create, edit, disable, remove, and price plans:

```text
/plan-add name:Starter price:1500 ram:4g cpu:1 disk:20g status:active
/plan-edit plan_id:1 price:2000
/plan-status plan_id:1 status:disabled
/plan-remove plan_id:1
/plans
/buy-item plan_id:1
/deploy-plan plan_id:1 location:SG
```

Plans are resource presets. Buying a plan grants one additional VPS slot; `/deploy-plan` charges the normal deployment fee and uses the plan resources.

## Protection

Protection settings are stored in `config/config.json` and the protection agent reloads this file automatically when it changes.

```text
/protection-config action:show
/protection-config action:set key:protection.auto_block value:true
/protection-config action:set key:protection.block_seconds value:1800
/protection-config action:set key:protection.max_blocks value:500
/protection-config action:set key:thresholds.connections_per_ip value:120
/protection-config action:set key:miner_detection.enabled value:true
```

Changes to firewall/protection thresholds are validated and written atomically.
