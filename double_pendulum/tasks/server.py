from datetime import datetime
import os
import socket
import subprocess
import time
import csv

#import beam_integrals as bi
#from beam_integrals.beam_types import BaseBeamType
#from beam_integrals.integrals import BaseIntegral
from celery import chain, chord
from celery.exceptions import Reject
import numpy as np


from ..app import app
from .worker import simulate_pendulum_instance


## Monitoring tasks

@app.task
def monitor_queues(ignore_result=True):
    server_name = app.conf.MONITORING_SERVER_NAME
    server_port = app.conf.MONITORING_SERVER_PORT
    metric_prefix = app.conf.MONITORING_METRIC_PREFIX

    queues_to_monitor = ('server', 'worker')
    
    output = subprocess.check_output('rabbitmqctl -q list_queues name messages consumers', shell=True)
    lines = (line.split() for line in output.splitlines())
    data = ((queue, int(tasks), int(consumers)) for queue, tasks, consumers in lines if queue in queues_to_monitor)

    timestamp = int(time.time())
    metrics = []
    for queue, tasks, consumers in data:
        metric_base_name = "%s.queue.%s." % (metric_prefix, queue)

        metrics.append("%s %d %d\n" % (metric_base_name + 'tasks', tasks, timestamp))
        metrics.append("%s %d %d\n" % (metric_base_name + 'consumers', consumers, timestamp))

    sock = socket.create_connection((server_name, server_port), timeout=10)
    sock.sendall(''.join(metrics))
    sock.close()


## Recording the experiment status
def get_experiment_status_filename(status):
    return os.path.join(app.conf.STATUS_DIR, status)

def get_experiment_status_time():
    """Get the current local date and time, in ISO 8601 format (microseconds and TZ removed)"""
    return datetime.now().replace(microsecond=0).isoformat()

@app.task
def record_experiment_status(status):
    with open(get_experiment_status_filename(status), 'w') as fp:
        fp.write(get_experiment_status_time() + '\n')



def parametric_sweep(L1, L2, m1, m2, theta_resolution, tmax, dt):
    for theta1_init in np.linspace(0, 2 * np.pi, theta_resolution):
        for theta2_init in np.linspace(0, 2 * np.pi, theta_resolution):
            yield L1, L2, m1, m2, tmax, dt, theta1_init, theta2_init

## Seeding the computations
@app.task
def seed_computations(ignore_result=True):
    #if os.path.exists(get_experiment_status_filename('started')):
    #    raise Reject('Computations have already been seeded!')

    record_experiment_status.si('started').delay()
    
    L1, L2 = 1.0, 1.0
    m1, m2 = 1.0, 1.0

    _tmax = app.conf.TMAX
    _theta_res = app.conf.THETA_RESOLUTION
    _dt = app.conf.DT

    chord(
        	(simulate_pendulum_instance.s(L1, L2, m1, m2, _tmax, _dt, theta1_init, theta2_init)
		for (L1, L2, m1, m2, _tmax, _dt, theta1_init, theta2_init) in 
		parametric_sweep(L1, L2, m1, m2, _theta_res, _tmax, _dt) 
		),	
	        store_pendulum_point.s() #store_computed_integral_tables.s()
    ).delay()


@app.task
def store_pendulum_point(results): #store_computed_integral_tables(results):
    filename = os.path.join(app.conf.RESULTS_DIR, 'results.csv')

    with open(filename, 'w') as resultsfile:
        fieldnames = ['theta1_init', 'theta2_init', 'theta1', 'theta2', 'x1', 'y1', 'x2', 'y2']
        writer = csv.DictWriter(resultsfile, fieldnames=fieldnames)
        writer.writeheader()

        for result in results:
            theta1_init, theta2_init, (theta1, theta2, x1, y1, x2, y2) = result
            writer.writerow({'theta1_init': theta1_init,
                             'theta2_init': theta2_init,
                             'theta1' : theta1[-1],
                             'theta2' : theta2[-1],
                             'x1' : x1[-1],
                             'y1' : y1[-1],
                             'x2' : x2[-1],
                             'y2' : y2[-1]})


