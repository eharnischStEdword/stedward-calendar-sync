# sync/validator.py
class SyncValidator:
    def validate_sync_result(self, source_events, target_events):
        """Validate that sync was successful"""
        validations = []
        
        # Check count matches
        public_source_count = len([e for e in source_events if 'Public' in e.get('categories', [])])
        target_count = len(target_events)
        validations.append(('count_match', abs(public_source_count - target_count) <= 1))
        
        # Check no private events leaked
        private_in_target = [e for e in target_events if 'Private' in e.get('categories', [])]
        validations.append(('no_private_leaked', len(private_in_target) == 0))
        
        return all(v[1] for v in validations), validations
