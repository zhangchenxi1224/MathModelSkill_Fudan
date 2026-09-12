"""Cost-aware local commitment and feedback-based global scheduling."""
import math
import time
from dataclasses import asdict
from bsolver.geometry import distance,minimum_enclosing_circle,max_distance
from bsolver.sensing import direction_outcome_bound
from bsolver.nosignal_sensing import positive_convex_visibility
from .clear_cover import build_cover
from .scheduling import closest_hull_point


class SynthesisMixin:
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.shared_used={f:0 for f in self.channels}
        self.stats.update(shared_current_measures=0,cost_gate_skips=0,confirmed16_transitions=0,
                          macro_lookahead_selections=0)
        self._entered_final_phase=False
        if self.spec.get('route_depth',1) not in (1,2): raise ValueError('route_depth must be 1 or 2')
        ratio=self.spec.get('shared_radius_ratio',.6)
        if not 0<ratio<1: raise ValueError('shared radius ratio must lie in (0,1)')
        for key in ('completion_aware','shared_current','cost_gate','refined_global','detour_by_known'):
            if key in self.spec and not isinstance(self.spec[key],bool): raise ValueError('flags must be boolean')

    def _known_count(self):
        return sum(k.status in ('detected','cleared') for k in self.channels.values())

    def _final_phase(self):
        known=self._known_count()
        if known>16: raise ValueError('confirmed source count contradicts public bound')
        active=self.spec.get('completion_aware',False) and known==16
        if active and not self._entered_final_phase:
            self._entered_final_phase=True; self.stats['confirmed16_transitions']+=1
            self._record('confirmed16_service_phase',confirmed_sources=known,
                         cleared_sources=self.stats['clear_successes'],
                         rule='stop looking for new channels; require actual success for all detected channels')
        return active

    def _select(self,knowledge):
        action,info=super()._select(knowledge)
        if self.spec.get('cost_gate',False) and action and action['kind']=='measure':
            try:
                points,certificate=build_cover(knowledge,self.client.position,'bbox')
            except (ValueError,ArithmeticError):
                return action,info
            # The direct plan's upper bound is compared to the unavoidable
            # movement + sensing + eventual success cost of this selected
            # measurement. This is a LOCAL service comparison, not global
            # dominance: finishing positions and future route may differ.
            lower=distance(self.client.position,action['point'])/5+5+int(self.client.current_channel!=knowledge.channel)+5
            upper=certificate['worst_case_cost_s']
            if upper+1e-5<lower:
                self.stats['cost_gate_skips']+=1
                decision=dict(direct_cover_upper_s=upper,measure_then_clear_lower_s=lower,
                              selected_measurement=action['point'],point_count=len(points))
                self._record('local_cost_gate',target_channel=knowledge.channel,decision=decision)
                return None,decision
        return action,info

    def _localize(self,channel):
        original=self.config.local_measure_limit
        self.config.local_measure_limit=max(0,original-self.shared_used[channel])
        try: return super()._localize(channel)
        finally: self.config.local_measure_limit=original

    def _shared_current(self):
        if not self.spec.get('shared_current',False): return
        for k in sorted(self._detected(),key=lambda k:k.channel):
            if self.shared_used[k.channel] or self.config.local_measure_limit<=0: continue
            if any(distance(self.client.position,o.position)<.05 for o in k.observations): continue
            _,radius=minimum_enclosing_circle(k.hull)
            if radius<=self.config.clear_radius: continue
            visible=(positive_convex_visibility(k.positive_positions,self.client.position) or
                     (self.config.problem==3 and max_distance(k.hull,self.client.position)<1000.-1e-5))
            if not visible:continue
            bound=direction_outcome_bound(k.hull,self.client.position,k.epsilon_deg,self.config.bin_width_deg)
            if bound<radius*self.spec.get('shared_radius_ratio',.6):
                self.shared_used[k.channel]+=1;self.stats['shared_current_measures']+=1
                self._record('shared_current_selection',target_channel=k.channel,prior_radius_m=radius,
                             direction_radius_bound_m=bound,consumes_local_measure_allowance=True)
                self._measure(k.channel,self.client.position,'shared_current_sensing')

    def _joint_service(self,next_point,force=False):
        original=self.config.joint_detour_m
        if self.spec.get('detour_by_known',False):
            known=self._known_count()
            factor=.5 if known<10 else 2. if known>=15 else 1.
            self.config.joint_detour_m=original*factor
            self._record('count_conditioned_detour',known_sources=known,factor=factor,
                         effective_detour_m=self.config.joint_detour_m,
                         role='ranking only; termination proof unchanged')
        try:return self._joint_service_impl(next_point,force)
        finally:self.config.joint_detour_m=original

    def _joint_service_impl(self,next_point,force=False):
        if self._final_phase(): next_point=None;force=True
        self._shared_current()
        if self.spec.get('route_depth',1)==1:
            return super()._joint_service(next_point,force)
        reorder,score=self._route_options()
        suffix=self.points.index(next_point) if next_point is not None else None
        if reorder and suffix is not None:next_point=self._route_reorder_suffix(suffix)
        serviced=set()
        while self._detected():
            choices=[]
            for k in self._detected():
                if k.channel in serviced:continue
                point,action_cost,kind=self._route_destination(k,score)
                detour=distance(self.client.position,point)
                if next_point is not None:
                    detour+=distance(point,next_point)-distance(self.client.position,next_point)
                choices.append((max(0.,detour),k.channel,point,action_cost,kind))
            if not choices:return
            # Three nearby macro service choices; finish the chosen target
            # before reconsidering another. Every root has equal depth.
            shortlist=sorted(choices)[:3]
            ranked=[]
            for detour,f,point,action_cost,kind in shortlist:
                if not force and detour>self.config.joint_detour_m:continue
                continuations=[]
                for _,other,_,other_cost,_ in shortlist:
                    if other==f:continue
                    destination=closest_hull_point(self.channels[other].hull,point)
                    length=distance(self.client.position,point)+distance(point,destination)
                    if next_point is not None:length+=distance(destination,next_point)
                    continuations.append((length/5+action_cost+other_cost,other))
                if continuations:value,second=min(continuations)
                else:value,second=detour/5+action_cost,None
                ranked.append((value,f,second,detour))
            if not ranked:return
            value,channel,second,detour=min(ranked)
            self.stats['macro_lookahead_selections']+=1
            self._record('macro_two_target_selection',target_channel=channel,second_channel_proxy=second,
                         score_s=value,first_detour_m=detour,score_status='geometric route proxy; no global bound')
            serviced.add(channel);self._localize(channel)
            if self._complete_evidence():return
            if self._final_phase():next_point=None;force=True;suffix=None
            if reorder and suffix is not None:next_point=self._route_reorder_suffix(suffix)

    def run(self):
        if not self.spec.get('completion_aware',False):
            return super().run()
        start = time.monotonic()
        status, error = "incomplete", None
        try:
            if not self.client.entered:
                self.client.enter()
            self._record("start", config=asdict(self.config), coverage_points=self.points)
            for i, point in enumerate(self.points):
                next_point = self.points[i+1] if i+1 < len(self.points) else None
                channels = [k.channel for k in self.channels.values() if k.status == "unknown"]
                if self.config.opportunistic_known_measurements:
                    channels += [k.channel for k in self._detected()]
                if self.client.current_channel in channels:
                    channels.remove(self.client.current_channel)
                    channels.insert(0, self.client.current_channel)
                for channel in channels:
                    if self._final_phase():
                        break
                    if self.channels[channel].status in ("cleared", "absent"):
                        continue
                    self._measure(channel, point, "global_coverage", station=i)
                    if self.config.scheduling == "immediate" and self.channels[channel].status == "detected":
                        self._localize(channel)
                    self.stop_evidence = self._complete_evidence()
                    if self.stop_evidence:
                        break
                if self.stop_evidence:
                    break
                if self.config.scheduling == "joint":
                    self._joint_service(next_point, force=next_point is None)
                self.stop_evidence = self._complete_evidence()
                if self.stop_evidence:
                    break
            if not self.stop_evidence:
                self._joint_service(None, force=True)
                self.stop_evidence = self._complete_evidence()
            if self.stop_evidence:
                status = "complete"
            else:
                error = "missing_completion_certificate"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._record("failure", error=error)
        finally:
            # Do not send new actions when an earlier action remains ambiguous.
            if self.client.entered and not self.client.exited and not getattr(self.client, "pending_request", None):
                try:
                    self.client.exit()
                except Exception as exc:
                    if status == "complete":
                        status = "complete_exit_unconfirmed"
                    error = (error+"; " if error else "")+f"exit: {type(exc).__name__}: {exc}"
        computed_time = (self.stats["walk_distance_m"]/5+self.stats["switches"]
                         +5*self.stats["measures"]+3*self.stats["clear_attempts"]
                         +2*self.stats["clear_successes"])
        result = {"status": status, "error": error, "problem": self.config.problem,
                  "config": asdict(self.config), **self.stats,
                  "total_virtual_time_s": self.client.virtual_time,
                  "independently_accounted_time_s": computed_time,
                  "timing_residual_s": self.client.virtual_time-computed_time,
                  "average_clear_time_s": (self.client.virtual_time/self.stats["clear_successes"]
                                           if self.stats["clear_successes"] else None),
                  "program_real_time_s": time.monotonic()-start,
                  "stop_evidence": self.stop_evidence,
                  "source_total": None, "clear_fraction": None}
        self._record("finish", result=result)
        return result
