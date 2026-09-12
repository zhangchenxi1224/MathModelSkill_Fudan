from calibrate import fit

def test_training_prior_has_normalized_masses_and_tracks_worlds():
    sources=[{'position':[100.+i*30,0.],'radius':1100.+i*20,
              'direction_deg':45. if i<4 else None} for i in range(10)]
    prior=fit([{'problem':4,'sources':sources,'_world_key':'training-example'}])
    assert abs(sum(prior['radius_mass'])-1)<1e-12
    assert abs(sum(prior['position_radial_mass'])-1)<1e-12
    assert prior['directional_prior']==5/12
    assert prior['training_world_keys']==['training-example']
