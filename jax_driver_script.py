import numpy as np
import math
from matplotlib import pyplot as plt
import time
import jax
import equinox as eqx
import diffrax
import jax.numpy as jnp
import optax
from sklearn.linear_model import LinearRegression
from itertools import permutations

jax.config.update("jax_enable_x64", True)

def get_linear_estimate(time_arc,Temperature_all,Q_exo_all,m,Cp,kb):


    T_start=Temperature_all[0]
    T_end_stage_1=440
    print("Note: using different end temperature to data for stage 2 to better initialize search space for stage 2")
    T_end=600#Temperature_all[-2] #600
    print("T_end:",T_end)
    

    h1_appx=m*Cp*(T_end_stage_1-T_start)
    #h2_appx=m*Cp*(T_end-T_end_stage_1)
    h2_appx=m*Cp*(Temperature_all[-1]-T_end_stage_1)

    # find break index
    for i in range(len(Temperature_all)):

        if Temperature_all[i] > T_end_stage_1:

            break_index=i
            break

    for i in range(len(Temperature_all)):

        if Temperature_all[i] > T_end:

            break_index_end=i
            break
    #first first stage info
    dTdt_stage=Q_exo_all[:break_index]
    T_stage=Temperature_all[:break_index]

    ln_dTdt=np.log(dTdt_stage)
    inv_T=1./T_stage
    
    inv_T=np.expand_dims(inv_T,axis=1)
    #ln_dTdt=np.expand_dims(ln_dTdt,axis=0)

    reg=LinearRegression().fit(inv_T,ln_dTdt)

    
    m=reg.coef_[0]
    c=reg.intercept_
    pred=m*inv_T+c

    #compute values:
    Ea1_appx=-kb*m
    A1_appx=np.exp(c)/(T_end_stage_1-T_start)
    print("Ea1_appx:",Ea1_appx)
    print("A1_appx:",A1_appx)
    print("h1_appx:",h1_appx)

    ''' 
    #plt.rcParams.update({'font.size': 13})
    plt.rcParams["font.weight"] = "bold"
    plt.rcParams["axes.labelweight"] = "bold"

    plt.plot(inv_T,ln_dTdt,label='Data')
    plt.plot(inv_T,pred,label='Linear Fit')
    plt.legend()
    plt.grid()
    plt.xlabel(r'1/T $(K^{-})$',fontsize=13)
    plt.ylabel(r'ln dT/dt $(K s^{-}) $ ',fontsize=13)
    plt.savefig('stage_1_fit.png')
    plt.close()
    '''
    # fit second stage info
    dTdt_stage=Q_exo_all[break_index:break_index_end]
    T_stage=Temperature_all[break_index:break_index_end]

    ln_dTdt=np.log(dTdt_stage)
    inv_T=1./T_stage
    inv_T=np.expand_dims(inv_T,axis=1)

    reg=LinearRegression().fit(inv_T,ln_dTdt)

    m=reg.coef_[0]
    c=reg.intercept_
    pred=m*inv_T+c

    #compute values:
    Ea2_appx=-kb*m
    A2_appx=np.exp(c)/(T_end-T_end_stage_1)
    print("Ea2_appx:",Ea2_appx)
    print("A2_appx:",A2_appx)
    print("h2_appx:",h2_appx)
    '''
    plt.plot(inv_T,ln_dTdt,label='data')
    plt.plot(inv_T,pred,label='fit')
    plt.legend()
    plt.savefig('stage_2_fit.png')
    plt.close()
    '''

    return [[A1_appx,Ea1_appx,h1_appx],[A2_appx,Ea2_appx,h2_appx]]

# preprocess ARC data before fitting    
def preprocess_data(time_arc,Temperature_all,Q_exo_all,fit_start_temp):

    for i in range(Temperature_all.shape[0]):
    
        if Temperature_all[i]> fit_start_temp:
            arg_break=i
            break
    time_arc=time_arc[arg_break:]
    Temperature_all=Temperature_all[arg_break:]
    Q_exo_all=Q_exo_all[arg_break:]
    
    # snip data to end Temperature (max)
    
    max_Temp_arg=np.argmax(Temperature_all)
    
    time_arc=time_arc[:max_Temp_arg+1]
    Temperature_all=Temperature_all[:max_Temp_arg+1]
    Q_exo_all=Q_exo_all[:max_Temp_arg+1]
    
    Temp_arc=Temperature_all
    
    # set start time =0
    time_arc=time_arc-time_arc[0]
   
    return time_arc,Temperature_all,Temp_arc,Q_exo_all

def scale_val(val,min_val,max_val):

    return 2.0*((val-min_val)/(max_val-min_val))-1.0

def unscale_val(val,min_val,max_val):

    return (1.0+val)*(max_val-min_val)/2.0+min_val

class stage():

    def __init__(self,typename,init_guess,m_val=None,n_val=None):
        kb = 1.380649E-23


        # these values are used in scaling the search variables
        self.log_max_A=jnp.log10(jnp.array(1E16))
        self.log_min_A=jnp.log10(jnp.array(1E10))

        self.log_max_Ea=jnp.log10(jnp.array(3.0E-19/kb))
        self.log_min_Ea=jnp.log10(jnp.array(1.5E-19/kb))

        self.log_min_h=jnp.log10(jnp.array(1E3))
        self.log_max_h=jnp.log10(jnp.array(1E5))

        self.min_m=jnp.array(1.0)
        self.max_m=jnp.array(8.0)

        self.min_n=jnp.array(1.0)
        self.max_n=jnp.array(8.0)
        
        # if the stage is of type 'kinetic' (i.e fit A, Ea, h)
        if typename=='kinetic':

            self.init_conc=1.0             
            self.m=0.0
            self.n=1.0
            #----------
            
            # scale the initial guess
            self.A=scale_val(jnp.log10(jnp.array(init_guess[0])),self.log_min_A,self.log_max_A)
            self.Ea=scale_val(jnp.log10(jnp.array(init_guess[1]/kb)),self.log_min_Ea,self.log_max_Ea)
            self.h=scale_val(jnp.log10(jnp.array(init_guess[2])),self.log_min_h,self.log_max_h)

        # if the stage is of type 'all' (i.e fit A, Ea, h, m, n)
        elif typename=='all':

            self.init_conc=0.04             
        
            
            # scale the initial guess
            self.A=scale_val(jnp.log10(jnp.array(init_guess[0])),self.log_min_A,self.log_max_A)
            self.Ea=scale_val(jnp.log10(jnp.array(init_guess[1]/kb)),self.log_min_Ea,self.log_max_Ea)
            self.h=scale_val(jnp.log10(jnp.array(init_guess[2])),self.log_min_h,self.log_max_h)

            self.m=scale_val(m_val,self.min_m,self.max_m)

            self.n=scale_val(n_val,self.min_n,self.max_n)



# construct ODE function (unique to two stage model)
# Arguments: 
# t: float (time)
# c: array (c1,c2,Temp)
# other_inputs_dict: dict['constants':dict,'all_vars':dict] (constants:static values, all_vars:dynamic values)
@jax.jit
def ode_fn(t,c,other_inputs_dict):

    constants=other_inputs_dict['constants']
    all_vars=other_inputs_dict['all_vars']

    n_stages=constants['num_stages']
    mass=constants['mass']
    eps=constants['eps']
    Cp=constants['Cp']

    c1=c[0] # concentration of first stage
    c2=c[1] # concentration of second stage
    Temp=c[2] # temperature

    # unscale from search space to physical space

    # parameters for stage 1
    unscaled_A1=jnp.power(10,unscale_val(all_vars['A1'],constants['log_min_A'],constants['log_max_A']))
    unscaled_Ea1=jnp.power(10,unscale_val(all_vars['Ea1'],constants['log_min_Ea'],constants['log_max_Ea']))
    unscaled_h1=jnp.power(10,unscale_val(all_vars['h1'],constants['log_min_h'],constants['log_max_h']))
    
    # parameters for stage 2
    unscaled_A2=jnp.power(10,unscale_val(all_vars['A2'],constants['log_min_A'],constants['log_max_A']))
    unscaled_Ea2=jnp.power(10,unscale_val(all_vars['Ea2'],constants['log_min_Ea'],constants['log_max_Ea']))
    unscaled_h2=jnp.power(10,unscale_val(all_vars['h2'],constants['log_min_h'],constants['log_max_h']))
    
    unscaled_m2=unscale_val(all_vars['m2'],constants['min_m'],constants['max_m'])
    unscaled_n2=unscale_val(all_vars['n2'],constants['min_n'],constants['max_n'])
    #--------

    # construct derivative terms particular to two stage model
    deriv1 = - jnp.power(c1,all_vars['n1'])*unscaled_A1*jnp.exp(-unscaled_Ea1/Temp) # m1=0, n1=1
    deriv_T= -unscaled_h1*deriv1/(Cp*mass)
    
    deriv2= jnp.power(c2,unscaled_n2)*jnp.power(1.0-c2,unscaled_m2)*unscaled_A2*jnp.exp(-unscaled_Ea2/Temp) # n2=0
    deriv_T+= unscaled_h2*deriv2/(Cp*mass)

    # include heat flux due to external factors
    T_inf=jnp.interp(t,constants['t_data'],constants['T_data'])
    h_conv=0.94115*(jnp.abs(T_inf-Temp)/0.07)**0.35
    Qdiss=constants['Acell']*(h_conv*(T_inf-Temp) + eps*constants['sigma']*(jnp.power(T_inf,4)-jnp.power(Temp,4)))/(Cp*mass) 
    deriv_T+=Qdiss
    return jnp.stack([deriv1,deriv2,deriv_T])
  

# integrate ODE system in time and get resulting loss value
# Arguments:
# constants: dict[]
# all_vars: dict[]

@jax.jit
def get_dTdt_loss(constants,all_vars):
    # start and end times
    t_init=constants['t_data'][0]
    t_end=constants['t_data'][-1]
    
    # initial condition
    y_init=jnp.array([1.0,0.04,397.0])

    # save at same time stamps as data
    saveat = diffrax.SaveAt(ts=constants['t_data'])
    term=diffrax.ODETerm(ode_fn)
    solution=diffrax.diffeqsolve(term,diffrax.Kvaerno5(),t0=t_init,t1=t_end,dt0 = 1.0,y0=y_init,max_steps=100000,saveat=saveat,args={'constants':constants,'all_vars':all_vars},stepsize_controller=diffrax.PIDController(pcoeff=0.3,icoeff=0.4,rtol=1e-8, atol=1e-8,dtmin=None))

    num_times=constants['t_data'].shape[0]

    T_hist=solution.ys[:,-1]
    #jax.debug.print("T history:{x}",x=T_hist)
    T_loss= jnp.sqrt(jnp.sum(jnp.square(T_hist-constants['T_data'])))/num_times

    return T_loss

# Main driver function
# Arguments: 
# constants: dict[]
# all_vars: dict[]
# trainable_variable_names: list[]
# n_iters: int
def main(constants,all_vars,trainable_variable_names,n_iters):
    # loss history
    loss_hist=[]

    # declare learning rate
    learning_rate=optax.exponential_decay(init_value=10**(-3),transition_steps=300,decay_rate=0.9,end_value=10**(-7))
    optimizer=optax.adam(learning_rate)
    
    train_val_dict={}
    apply_grad_dict={}
    # some variables may not be being trained

    for name in trainable_variable_names:

        train_val_dict[name]=all_vars[name]

    # the optimizer tracks the variables 
    opt_state=optimizer.init(train_val_dict)


    # run iterations
    for i in range(n_iters):

        # compute loss value and gradients
        value,grad_loss=jax.value_and_grad(get_dTdt_loss,argnums=1)(constants,all_vars)

        loss_hist.append(value)
        if i%100==0:
            print("Iteration:,loss value:",i,value)
       
        for name in trainable_variable_names:
            
            apply_grad_dict[name]=grad_loss[name]

        # get optimizer updates
        updates,opt_state=optimizer.update(apply_grad_dict,opt_state)

        # apply updates to optimizer variables, including trainable network variables
        results=optax.apply_updates(train_val_dict,updates)

        # update variable list used in ODE function 
        all_vars.update(results) 
        # update variable list tracked by optimizer
        train_val_dict.update(results)

    plt.plot(loss_hist)
    plt.savefig('loss_plot.png')
    plt.close()

    return value,all_vars

if __name__=="__main__":

    #constants
    Cp=jnp.array(859.0)
    Acell=jnp.array(4.618E-3)
    mass=jnp.array(0.066)
    eps=jnp.array(0.8)
    sigma=jnp.array(5.67037442e-8)
    kb=jnp.array(1.380649E-23)


    num_stages=2
    stages_types=['kinetic','all']

    # Initial conditions
    fit_start_temp=397.0
    
    data=np.genfromtxt('data_file.csv',delimiter=',')
    time_arc=data[:,0]
    Temperature_all=data[:,1]
    Q_exo_all=data[:,2]

    #--------
    #  
    n_iters=10000
    # guessed initial conditions of m,n
    m_val=5.0
    n_val=0.0

    #--------
    # write list of variables that should be trained. These may or may not be all possible variables

    trainable_variable_names=['A1','Ea1','h1','A2','Ea2','h2','m2']

    #--------

    # all losses list
    all_losses=[]

    # all params list
    all_params=[]

    time_arc,Temperature_all,Temp_arc,Q_exo_all=preprocess_data(time_arc,Temperature_all,Q_exo_all,fit_start_temp)
    t_init=jnp.array(time_arc[0])

    # linear estimate of fit to get initial guess
    linear_estimate=get_linear_estimate(time_arc,Temperature_all,Q_exo_all,mass,Cp,kb)

    #------------------------------------------
    # create a list of all stage objects
    # stages list

    stages_list=[]
    for i,stagename in enumerate(stages_types):

        stage_obj=stage(stagename,linear_estimate[i],m_val,n_val)
        stages_list.append(stage_obj)

    #setup initial conditions tensor
    #---------------------------
    init_cond=[]
    for stage_obj in stages_list:
        init_cond.append(stage_obj.init_conc)

    init_cond.append(Temperature_all[0])

    #---------------------------
    #collect indices of differentiable params
    diff_list=[]
    
    # setup constants dictionary
    # experimental data
    constants={'Acell':Acell,'mass':mass,'Cp':Cp,'eps':eps,'sigma':sigma}
    constants['t_data']=jnp.array(time_arc)
    constants['T_data']=jnp.array(Temperature_all)
    constants['dTdt_data']=jnp.array(Q_exo_all) 
    constants['c_init']= init_cond
    constants['num_stages'] = num_stages 
    #add scaling values in
    constants['log_max_A']=stage_obj.log_max_A
    constants['log_min_A']=stage_obj.log_min_A
    constants['log_max_Ea']=stage_obj.log_max_Ea
    constants['log_min_Ea']=stage_obj.log_min_Ea
    constants['log_max_h']=stage_obj.log_max_h
    constants['log_min_h']=stage_obj.log_min_h
    constants['min_m']=stage_obj.min_m
    constants['max_m']=stage_obj.max_m
    constants['min_n']=stage_obj.min_n
    constants['max_n']=stage_obj.max_n

    # setup all variables dictionary. These are all variables that can be trained
    all_vars={}

    for i_stage,stage_obj in enumerate(stages_list):
        stage_no=str(i_stage+1)

        all_vars['A'+stage_no]=stage_obj.A

        all_vars['Ea'+stage_no]=stage_obj.Ea
        all_vars['h'+stage_no]=stage_obj.h
        all_vars['m'+stage_no]=stage_obj.m
        all_vars['n'+stage_no]=stage_obj.n


    loss_val,trained_vars=main(constants,all_vars,trainable_variable_names,n_iters)


    print("Fitted Parameters:",trained_vars)

