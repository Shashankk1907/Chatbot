#!/bin/bash
# scripts/init_sharding.sh
# 
# Initializes a local MongoDB sharded cluster.
# Run this AFTER starting containers with docker-compose.mongo.yml.

echo "Waiting for containers to start..."
sleep 5

# 1. Initialize Config Server Replica Set
echo "Initializing Config Server Replica Set..."
docker exec -it mongo-configsvr mongosh --port 27019 --eval '
  rs.initiate({
    _id: "configRS",
    configsvr: true,
    members: [{ _id: 0, host: "configsvr:27019" }]
  })
'

# 2. Initialize Shard 1 Replica Set
echo "Initializing Shard 1 Replica Set..."
docker exec -it mongo-shard1 mongosh --port 27018 --eval '
  rs.initiate({
    _id: "shard1RS",
    members: [{ _id: 0, host: "shard1:27018" }]
  })
'

# 3. Initialize Shard 2 Replica Set
echo "Initializing Shard 2 Replica Set..."
docker exec -it mongo-shard2 mongosh --port 27020 --eval '
  rs.initiate({
    _id: "shard2RS",
    members: [{ _id: 0, host: "shard2:27020" }]
  })
'

echo "Waiting for replica sets to stabilize..."
sleep 10

# 4. Add Shards to Mongos Router
echo "Adding Shards to Mongos Router..."
docker exec -it mongo-router mongosh --port 27017 --eval '
  sh.addShard("shard1RS/shard1:27018");
  sh.addShard("shard2RS/shard2:27020");
'

echo "Sharding initialization complete!"
echo "Next: run python -m db.mongo_setup to enable sharding for specific collections."
