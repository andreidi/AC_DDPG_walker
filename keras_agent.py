# -*- coding: utf-8 -*-

import numpy as np
import random
from collections import namedtuple, deque

from tensorflow.keras import layers, models, optimizers, initializers, regularizers
import tensorflow.keras.backend as K
import tensorflow as tf



class KAgent:
  """
  implementation of continuous state env agent based on
  Deterministic Deep Policy Gradient (DDPG) and Twin Delayed DDPG (TD3)
  
  https://arxiv.org/pdf/1802.09477.pdf
  https://arxiv.org/pdf/1509.02971.pdf
  
  """
  def __init__(self, state_size, action_size, BUFFER_SIZE=int(1e6), 
               BATCH_SIZE=128, RANDOM_WARM_UP=512,
               env=None, random_seed=1234, GAMMA=0.99, TAU=5e-3, 
               TD3=True, 
               policy_update_freq=2, noise_clip=0.5, 
               policy_noise=0.2, explor_noise=0.1,
               
               name='agent',
               
               actor_layer_noise=0,
               actor_batch_norm=False,
               actor_activation='relu',
               actor_layer_reg=1e-3,
               actor_lr=1e-4,
               actor_clip_norm=None,
               
               critic_layer_noise=0.1,
               critic_batch_norm=True,
               critic_activation='leaky',
               critic_layer_reg=1e-3,
               critic_lr=1e-3,
               critic_clip_norm=1,
               ):
    self.TD3 = TD3
    self.env = env

    self.RANDOM_WARM_UP = RANDOM_WARM_UP
    self.BUFFER_SIZE = BUFFER_SIZE
    self.BATCH_SIZE = BATCH_SIZE
    self.GAMMA = GAMMA
    self.TAU = TAU
    self.name = name
    self.state_size = state_size

    self.actor_layer_noise = actor_layer_noise
    self.actor_layer_reg = actor_layer_reg
    self.actor_batch_norm = actor_batch_norm
    self.actor_activation = actor_activation
    self.actor_lr = actor_lr
    self.actor_clip_norm = actor_clip_norm

    self.critic_layer_reg = critic_layer_reg
    self.critic_layer_noise = critic_layer_noise
    self.critic_batch_norm = critic_batch_norm
    self.critic_activation = critic_activation
    self.critic_lr = critic_lr
    self.critic_clip_norm = critic_clip_norm

    self.action_size = action_size

    self.actor_online = self._define_actor_model(state_size, action_size)
    self.actor_target = self._define_actor_model(state_size, action_size)
    _co, _cof = self._define_critic_model(state_size, action_size, 
                                          output_size=1, 
                                          compile_model=True)
    self.critic_online_1 = _co
    self.critic_online_frozen = _cof
    self.critic_target_1, _ = self._define_critic_model(state_size, action_size, 
                                                        output_size=1, 
                                                        compile_model=False)
    
    
    self.critic_online_2, _ = self._define_critic_model(state_size, action_size, 
                                                        output_size=1, 
                                                        compile_model=True)
    self.critic_target_2, _ = self._define_critic_model(state_size, action_size, 
                                                        output_size=1, 
                                                        compile_model=False)
    ###
    ### init models
    ###
    self.soft_copy_actor(tau=1)
    self.soft_copy_critics(tau=1)
    ###

    
    if self.TD3:
      self.policy_update_freq = policy_update_freq
      self.policy_noise = policy_noise
      self.noise_clip = noise_clip
      self.explor_noise = explor_noise
      
    self.actor_trainer = self._define_actor_train_func1()
    
    self.max_action = 1 if self.env is None else self.env.action_space.high.max()
    self.min_action = -1 if self.env is None else self.env.action_space.low.min()
    
    # Noise process
    self.noise = OUNoise(action_size, random_seed)

    # Replay memory
    self.memory = ReplayBuffer(action_size, self.BUFFER_SIZE, 
                               BATCH_SIZE, random_seed)
    self.critic_losses = []
    self.actor_losses = []

    self.train_iters = 0
    self.actor_updates = 0
    self.step_counter = 0
    self.steps_to_train_counter = 0
    self.skip_update_timer = 0

    print("Actor DAG:")
    self.actor_online.summary()
    print("Critic DAG:")
    self.critic_online_1.summary()
    print("Agent '{}' initialized with following params:\n {}".format(
        self.name, self.get_str()))
    return


  def get_str(self):
    obj = self
    out_str = obj.__class__.__name__+"("
    for prop, value in vars(obj).items():
      if type(value) in [int, float, bool]:
        out_str += prop+'='+str(value) + ','
      elif type(value) in [str]:
        out_str += prop+"='" + value + "',"
    out_str = out_str[:-1] if out_str[-1]==',' else out_str
    out_str += ')'
    return out_str
  
  
  def reset(self):
    self.noise.reset()

    
  def is_warming_up(self):
    return len(self.memory) < self.RANDOM_WARM_UP
    

  def step(self, state, action, reward, next_state, done, 
           train_every_steps):
    """Save experience in replay memory. train if required"""
    # Save experience / reward
    self.step_counter += 1
    self.memory.add(state, action, reward, next_state, done)

    if self.steps_to_train_counter > 0:
      self.train(nr_iters=1)
      self.steps_to_train_counter -= 1
      self.skip_update_timer = 0
    else:
      self.skip_update_timer += 1
        
    if self.skip_update_timer >= train_every_steps:
      self.steps_to_train_counter = train_every_steps
      self.skip_update_timer = 0
            
    return

  
  def train(self, nr_iters):
    """ use random sample from buffer to learn """
    # Learn, if enough samples are available in memory
    if len(self.memory) > self.RANDOM_WARM_UP:
      for _ in range(nr_iters):
        experiences = self.memory.sample()
        self._train(experiences, self.GAMMA)
  

  def act(self, state, add_noise=False):
    """Returns actions for given state as per current policy."""
    if len(state.shape) == 1:
      state = state.reshape((1,-1))
    action = self.actor_online.predict(state)
    if add_noise:
      # we are obviously in training so now check if the "act" was called before warmpup
      assert not self.is_warming_up()
      if self.TD3:
        noise = np.random.normal(loc=0, scale=self.explor_noise, 
                                 size=action.shape)
      else:
        noise = self.noise.sample()
      action += noise
    return np.clip(action, self.min_action, self.max_action)


  def _train(self, experiences, gamma):
    """Update policy and value parameters using given batch of experience tuples.
    Q_targets = r + γ * critic_target(next_state, actor_target(next_state))
    where:
        actor_target(state) -> action
        critic_target(state, action) -> Q-value

    Params
    ======
        experiences: tuple of (s, a, r, s', done) tuples 
        gamma (float): discount factor
    """
    self.train_iters += 1 # increment update
    
    states, actions, rewards, next_states, dones = experiences

    # ---------------------------- update critic ---------------------------- #
    # Get predicted next-state actions and Q values from target models
    actions_next = self.actor_target.predict(next_states)
    
    if self.TD3:
      noise = np.random.normal(loc=0.0, scale=self.policy_noise,
                               size=actions_next.shape)
      noise = np.clip(noise, -self.noise_clip, self.noise_clip)
      actions_next += noise
      actions_next = np.clip(actions_next, self.min_action, self.max_action)
    
    Q_targets_next_1 = self.critic_target_1.predict([next_states, actions_next])
    
    if self.TD3:
      Q_targets_next_2 = self.critic_target_2.predict([next_states, actions_next])    
      Q_targets_next = np.minimum(Q_targets_next_1, Q_targets_next_2)
    else:
      Q_targets_next = Q_targets_next_1
    
    # Compute Q targets for current states (y_i)
    Q_targets = rewards + (gamma * Q_targets_next * (1 - dones))    
    
    # Train critic online
    critic_loss_1 = self.critic_online_1.train_on_batch(x=[states, actions], 
                                                        y=Q_targets)
    if self.TD3:
      critic_loss_2 = self.critic_online_2.train_on_batch(x=[states, actions], 
                                                          y=Q_targets)
      critic_loss = critic_loss_1 + critic_loss_2
    else:
      critic_loss = critic_loss_1
      
    self.critic_losses.append(critic_loss)
    
    if self.TD3 and (self.train_iters % self.policy_update_freq) != 0:
      # not yet update so skip the policy update and the targets update
      return
    
    # ---------------------------- update actor ---------------------------- #
    self.actor_updates += 1
    # now update actor after critic update
    actor_loss = self.actor_trainer([states])
    actor_loss = actor_loss[0] if isinstance(actor_loss, list) else actor_loss
    self.actor_losses.append(actor_loss)

    # ----------------------- update target networks ----------------------- #
    self.soft_update(target=self.actor_target,  source=self.actor_online,  tau=self.TAU)                     
    self.soft_update(target=self.critic_target_1, source=self.critic_online_1, tau=self.TAU)
    if self.TD3:
      self.soft_update(target=self.critic_target_2, source=self.critic_online_2, tau=self.TAU)
    return


  def save_actor(self, label):
    fn = '{}_{}.policy'.format(
        self.name, label)
    print("\n  Saving  '{}'".format(fn), flush=True)
    self.actor_online.save(fn)
    return fn


  def load_actor(self, fn):
    print("\nLoading actor '{}'".format(fn))
    self.actor_online = tf.keras.models.load_model(fn)
    return

  ### ToDo:
  ### - add weight decay 1e-2
  ### - add grad clip in optimizer norm=1
  ###
  ###
  ### - run train multiple times @ each iteration ???
  ###
  ###
  ###
  
  def _define_actor_train_func1(self, lr=1e-4):
    """
    alternative #1 of actor training 
    """
    opt = optimizers.Adam(lr=lr)
    tf_input = layers.Input((self.state_size,), name='actor_trainer_input')
    tf_act = self.actor_online(tf_input)
    tf_Q = self.critic_online_1([tf_input, tf_act])
    tf_loss = -K.mean(tf_Q)
    tf_updates = opt.get_updates(loss=tf_loss, 
                                 params=self.actor_online.trainable_variables)
    train_func = K.function(inputs=[tf_input], 
                            outputs=[tf_loss], 
                            updates=tf_updates)
    return train_func
  
  def _define_actor_trainer(self):
    """
    alternative #2 of actor training 
    """
    tf_input = layers.Input((self.state_size,), name='actor_trainer_input')
    tf_actions_pred = self.actor_online(tf_input)
    # next line forces dQ / da so we can then propagate this grad in online actor
    tf_critic_values = self.critic_online_frozen([tf_input, tf_actions_pred])
    def actor_loss(y_true, y_pred):
      tf_loss = -K.mean(y_pred)
      return tf_loss
    if self.actor_clip_norm:
      opt = optimizers.Adam(lr=self.actor_lr, 
                            clipnorm=self.actor_clip_norm)
    else:
      opt = optimizers.Adam(lr=self.actor_lr)
    self.actor_trainer_model = models.Model(inputs=tf_input, outputs=tf_critic_values)
    self.actor_trainer_model.compile(loss=actor_loss, optimizer=opt)
    trainer_func = lambda x: self.actor_trainer_model.train_on_batch(x=x[0],y=None)
    return trainer_func
    
    
  
  def _define_actor_model(self, input_size, output_size):
    if not isinstance(input_size,tuple):
      input_size = (input_size,)
    tf_input = layers.Input(input_size, name='actor_input')
    tf_x = tf_input

    tf_x = self._fc_layer(tf_x=tf_x, output=64, 
                          activation=self.actor_activation,
                          bn=self.actor_batch_norm,
                          reg=self.actor_layer_reg,
                          noise=self.actor_layer_noise,
                          init_uniform=False,
                          name='a_main_1')
    
    tf_x = self._fc_layer(tf_x=tf_x, output=32,  # next matrix is 300x1 and rand_unif_init 3e-3
                          activation=self.actor_activation,
                          bn=self.actor_batch_norm,
                          reg=self.actor_layer_reg,
                          noise=self.actor_layer_noise,
                          init_uniform=False,
                          name='a_main_2')
    
    tf_x = self._fc_layer(tf_x=tf_x, output=output_size, 
                          activation='tanh',
                          bn=False,
                          reg=self.actor_layer_reg,
                          noise=False,
                          init_uniform=True,
                          name='a_output')
    
    model = models.Model(inputs=tf_input, outputs=tf_x, name='actor')
    return model
  
  def _fc_layer(self, tf_x, output, activation, bn, reg, noise, name, init_uniform):    
    assert activation in ['linear', 'leaky', 'relu', 'tanh']
    ker_reg = regularizers.l2(reg) if reg > 0 else None
    ker_init = initializers.RandomUniform(-3e-3,3e-3) if init_uniform else 'glorot_uniform'
    tf_x = layers.Dense(output, name=name+"_dns", 
                        kernel_regularizer=ker_reg,
                        kernel_initializer=ker_init)(tf_x)
    if activation != 'linear':
      if bn:
        tf_x = layers.BatchNormalization(name=name+"_bn")(tf_x)
      if activation == 'leaky':
        tf_x = layers.LeakyReLU(name=name+"_Lrel")(tf_x)
      else:
        tf_x = layers.Activation(activation, name=name+"_"+activation)(tf_x)
      if noise>0:
        tf_x = layers.GaussianNoise(stddev=noise, name=name+"_gnoise")(tf_x)
    return tf_x
    
    
  def _define_critic_model(self, input_size, action_size, output_size, 
                           output_activation=None, compile_model=True):
    if not isinstance(input_size,tuple):
      input_size = (input_size,)
    if not isinstance(action_size, tuple):
      action_size = (action_size,)
    tf_input_state = layers.Input(input_size, name='c_input_state')
    tf_input_action = layers.Input(action_size, name='c_input_action')
    tf_x_s = tf_input_state
        
    tf_x_s = self._fc_layer(tf_x=tf_x_s, output=64, 
                            activation=self.critic_activation,
                            bn=self.critic_batch_norm,
                            reg=self.critic_layer_reg,
                            noise=self.critic_layer_noise,
                            init_uniform=False,
                            name='c_state')
    tf_x_a = tf_input_action
    
    tf_x = layers.concatenate([tf_x_s, tf_x_a], name='c_concat_sa')
    
    tf_x = self._fc_layer(tf_x=tf_x, output=32, 
                          activation=self.critic_activation,
                          bn=self.critic_batch_norm,
                          reg=self.critic_layer_reg,
                          noise=self.critic_layer_noise,
                          init_uniform=False,
                          name='c_main')

    tf_x = self._fc_layer(tf_x=tf_x, output=1, 
                          activation='linear',
                          bn=self.critic_batch_norm,
                          reg=self.critic_layer_reg,
                          noise=self.critic_layer_noise,
                          init_uniform=True,
                          name='c_output')
    
    model = models.Model(inputs=[tf_input_state,tf_input_action], outputs=tf_x,
                         name='critic')
    
    if compile_model:
      if self.critic_clip_norm:
        opt = optimizers.Adam(lr=self.critic_lr, 
                              clipnorm=self.critic_clip_norm)    
      else:
        opt = optimizers.Adam(lr=self.critic_lr, )
      model.compile(loss='mse', optimizer=opt)
    
    model_frozen = models.Model(inputs=[tf_input_state,tf_input_action], outputs=tf_x,
                         name='frozen_critic')
    model_frozen.trainable = False
    return model, model_frozen
  
  def soft_update(self, target, source, tau):
    wt = target.get_weights()
    ws = source.get_weights()
    wf = [wt[i] * (1 - tau) + ws[i] * tau for i in range(len(wt))]
    target.set_weights(wf)
    return
  
  def soft_copy_actor(self, tau=0.005):
    self.soft_update(self.actor_target, self.actor_online, tau=tau)
    
  def soft_copy_critics(self, tau=0.005):
    self.soft_update(self.critic_target_1, self.critic_online_1, tau=tau)
    if self.TD3:
      self.soft_update(self.critic_target_2, self.critic_online_2, tau=tau)



class OUNoise:
  """Ornstein-Uhlenbeck process."""

  def __init__(self, size, seed, mu=0., theta=0.15, sigma=0.2, dt=1e-2):
    """Initialize parameters and noise process."""
    self.mu = mu * np.ones(size)
    self.theta = theta
    self.sigma = sigma
    self.dt = dt
    self.seed = random.seed(seed)
    self.reset()

  def reset(self):
    """Reset the internal state (= noise) to mean (mu)."""
    self.state = self.mu.copy()

  def sample(self):
    """Update internal state and return it as a noise sample."""
    x = self.state
    dx = self.theta * (self.mu - x) * self.dt + self.sigma * np.sqrt(self.dt) * np.random.randn(len(x))
    self.state = x + dx
    return self.state




class ReplayBuffer:
  """Fixed-size buffer to store experience tuples."""

  def __init__(self, action_size, buffer_size, batch_size, seed):
    """Initialize a ReplayBuffer object.
    Params
    ======
        buffer_size (int): maximum size of buffer
        batch_size (int): size of each training batch
    """
    self.action_size = action_size
    self.memory = deque(maxlen=buffer_size)  # internal memory (deque)
    self.batch_size = batch_size
    self.experience = namedtuple("Experience", field_names=["state", "action", "reward", "next_state", "done"])
    self.seed = random.seed(seed)
  
  def add(self, state, action, reward, next_state, done):
    """Add a new experience to memory."""
    e = self.experience(state, action, reward, next_state, done)
    self.memory.append(e)
  
  def sample(self):
    """Randomly sample a batch of experiences from memory."""
    experiences = random.sample(self.memory, k=self.batch_size)

    states = np.vstack([e.state for e in experiences if e is not None])
    actions = np.vstack([e.action for e in experiences if e is not None])
    rewards = np.vstack([e.reward for e in experiences if e is not None])
    next_states = np.vstack([e.next_state for e in experiences if e is not None])
    dones = np.vstack([e.done for e in experiences if e is not None]).astype(np.uint8)

    return (states, actions, rewards, next_states, dones)

  def __len__(self):
    """Return the current size of internal memory."""
    return len(self.memory)  
  
if __name__ == '__main__':
  
  act = KAgent(state_size=24, action_size=4,)
  
  m1 = act.actor_online
  m2 = act.actor_target
  print("Actor:")
  print("Mean of online weights {}".format([x.mean() for x in m1.get_weights()]))
  print("Mean of target weights {}".format([x.mean() for x in m2.get_weights()]))
  modw = [x * 2 for x in m2.get_weights()]
  print("Modifying target weights")
  m2.set_weights(modw)
  print("Mean of target weights {}".format([x.mean() for x in m2.get_weights()]))
  tau = 0.5
  print("Soft update target with tau={}".format(tau))
  act.soft_copy_actor(tau=tau)
  print("Mean of target weights {}".format([x.mean() for x in m2.get_weights()]))
  
  print("Critic:")
  m1 = act.critic_online_1
  m2 = act.critic_target_1
  print("Mean of online weights {}".format([x.mean() for x in m1.get_weights()]))
  print("Mean of target weights {}".format([x.mean() for x in m2.get_weights()]))
  modw = [x * 2 for x in m2.get_weights()]
  print("Modifying target weights")
  m2.set_weights(modw)
  print("Mean of target weights {}".format([x.mean() for x in m2.get_weights()]))
  tau = 0.5
  print("Soft update target with tau={}".format(tau))
  act.soft_copy_critics(tau=tau)
  print("Mean of target weights {}".format([x.mean() for x in m2.get_weights()]))
    