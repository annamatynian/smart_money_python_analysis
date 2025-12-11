"""
INTEGRATION INSTRUCTIONS FOR services.py

Copy-paste the code blocks below into your services.py file.
Search for the markers (# MARKER 1, # MARKER 2) to find exact locations.
"""

# ==============================================================================
# INTEGRATION POINT 1: TradeEvent Handler
# LOCATION: Search for "iceberg_event = IcebergAnalyzer.analyze"
# LINE: approximately 238
# ==============================================================================

# FIND THIS CODE:
# iceberg_event = IcebergAnalyzer.analyze(self.book, trade, target_vol)

# REPLACE WITH THIS:
"""
# === NEW DELTA-T LOGIC ===
# WHY: Instead of analyzing immediately, we add to pending queue
# and wait for corresponding OrderBookUpdate to calculate Delta-t

self.book.pending_refill_checks.append({
    'trade': trade,
    'visible_before': target_vol,
    'trade_time_ms': trade.event_time,  # Exchange timestamp
    'price': trade.price,
    'is_ask': not trade.is_buyer_maker
})

# Cleanup entries older than 100ms (prevent memory leak)
self._cleanup_pending_checks(current_time_ms=trade.event_time)

# NOTE: ML LOGIC block below should remain unchanged!
# Keep all code from line ~245-290 as-is
"""


# ==============================================================================
# INTEGRATION POINT 2: OrderBookUpdate Handler
# LOCATION: Search for "if self.book.apply_update(event):"
# LINE: approximately 180
# ==============================================================================

# FIND THIS CODE:
# if self.book.apply_update(event):
#     if not self.book.validate_integrity():

# ADD THIS CODE RIGHT AFTER "if self.book.apply_update(event):":
"""
            # === NEW: Delta-t Iceberg Detection ===
            # WHY: Check if any pending trades were refilled by this update
            
            update_time_ms = int(update.event_time.timestamp() * 1000)
            
            # Iterate through all pending checks
            for pending in list(self.book.pending_refill_checks):
                trade = pending['trade']
                
                # Filter 1: Same price level?
                if pending['price'] != trade.price:
                    continue
                
                # Filter 2: Calculate Delta-t
                delta_t = update_time_ms - pending['trade_time_ms']
                
                # Reject race conditions (update arrived before trade)
                if delta_t < -20:
                    continue
                
                # Remove stale entries (older than 100ms)
                if delta_t > 100:
                    self.book.pending_refill_checks.remove(pending)
                    continue
                
                # Filter 3: Did volume restore? (refill indicator)
                current_vol = self._get_volume_at_price(trade.price, pending['is_ask'])
                
                # If volume >= visible_before, this is a refill candidate
                if current_vol >= pending['visible_before']:
                    
                    # CRITICAL: Call new method with Delta-t validation
                    iceberg_event = IcebergAnalyzer.analyze_with_timing(
                        book=self.book,
                        trade=trade,
                        visible_before=pending['visible_before'],
                        delta_t_ms=delta_t,
                        update_time_ms=update_time_ms
                    )
                    
                    # If genuine iceberg detected, print alert
                    if iceberg_event:
                        lvl = self.book.active_icebergs.get(trade.price)
                        total_hidden = lvl.total_hidden_volume if lvl else iceberg_event.detected_hidden_volume
                        obi = self.book.get_weighted_obi(depth=20)
                        self._print_iceberg_update(iceberg_event, total_hidden, obi, lvl)
                        
                        # Save to database if repository available
                        if self.repository and lvl:
                            asyncio.create_task(self.repository.save_level(lvl, self.symbol))
                    
                    # Remove processed check from queue
                    self.book.pending_refill_checks.remove(pending)
            
            # Continue with existing integrity check...
"""

# IMPORTANT: The integrity check code should follow immediately:
# if not self.book.validate_integrity():
#     print("❌ Book integrity failed! Resyncing...")
#     await self._resync()
#     break
